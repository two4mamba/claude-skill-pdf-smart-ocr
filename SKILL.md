---
name: pdf-smart-ocr
description: Smart PDF → Markdown extractor that picks the best engine automatically. Use when the user asks to extract / OCR / convert / parse a PDF (especially scanned, image-based, or PowerPoint-export PDFs) into markdown text. Routes between markitdown (for text PDFs), pdftoppm + vision (for small image PDFs ≤ 10 pages), and MinerU CLI (for large image PDFs > 10 pages). Preserves layout, tables, formulas, headings.
---

# pdf-smart-ocr

A decision-layer skill that converts any PDF into clean Markdown by picking the right engine for the file.

## When to invoke

Trigger this skill when the user asks any of:
- "把 X.pdf 转成 markdown / 提取出来"
- "OCR 这份 PDF"
- "把扫描件 / 截图 PDF 识别成文字"
- "提取 PPT 导出的 PDF 内容"
- Anything matching: extract, OCR, parse, convert PDF → text/markdown.

## Decision tree

```
┌──────────────────────────────────────────┐
│ Input PDF                                │
└────────────────┬─────────────────────────┘
                 │
        run classify.py
                 │
   ┌─────────────┼─────────────────────────┐
   │             │                          │
text PDF    image, ≤10 pages         image, >10 pages
   │             │                          │
markitdown   pdftoppm + Read              mineru CLI
   │         (vision read)               (local OCR/VLM)
   │             │                          │
   └─────────────┴───────────► Markdown output
```

## Required environment

The skill assumes these are already installed (this machine has them):

| Tool | Purpose | Verify |
|------|---------|--------|
| `pdftoppm` (poppler) | Render PDF → PNG | `pdftoppm -v` |
| `mineru` CLI | Heavy-duty OCR/VLM parsing | `mineru --version` |
| `markitdown` Python pkg | Fast text-PDF conversion | `python -c "import markitdown"` |
| `pdfplumber` Python pkg | Text-layer probing | `python -c "import pdfplumber"` |

If a tool is missing, tell the user which one and stop.

## Step-by-step procedure

### Step 1 — Classify

Run:

```bash
python "<SKILL_DIR>/scripts/classify.py" "<absolute-pdf-path>"
```

It prints a single JSON line, e.g.:

```json
{"pages": 142, "sampled_pages": 10, "text_chars": 21, "avg_chars_per_page": 2, "has_text_layer": false, "recommendation": "image_large"}
```

Possible recommendations: `text`, `image_small`, `image_large`.

### Step 2 — Dispatch

Choose based on `recommendation`:

#### A. `text` → markitdown

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode text --pdf "<pdf>" --out "<out_dir>"
```

Produces `<out_dir>/<pdf-stem>.md`. Fast (seconds).

#### B. `image_small` → render + vision

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode image_small --pdf "<pdf>" --out "<out_dir>"
```

This renders pages to `<out_dir>/_pages/p001.png …`. You (Claude) MUST then:
1. Read each PNG with the `Read` tool (vision-capable).
2. Combine the visual reading into a structured markdown file at `<out_dir>/<pdf-stem>.md`.
3. Delete `<out_dir>/_pages/` after writing the .md (free disk).

Use this path for ≤10 pages — token cost is acceptable, quality is highest.

#### C. `image_large` → MinerU (auto-chunked)

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode image_large --pdf "<pdf>" --out "<out_dir>"
```

Internally:
- If pages ≤ `--chunk-size` (default 50), MinerU runs once.
- If pages > chunk-size, the PDF is processed in sequential page ranges to avoid OOM (32GB RAM laptops can OOM on 100+ pages in one shot). Per-chunk outputs are merged and intermediate dirs cleaned.

**Final output layout** (clean, one file + images):

```
<out_dir>/
├── <pdf-stem>.md       # merged markdown
└── images/             # all extracted figures, deduplicated by content hash
```

Backend choice:
- `pipeline` (default): CPU-friendly, PP-OCRv5+v4 PyTorch port.
- `hybrid-auto-engine`: highest quality, needs GPU.
- `vlm-auto-engine`: VLM-based, also needs GPU.

If you OOM, drop `--chunk-size` to 25 or 10. Use `--keep-intermediate` to debug.

### Step 3 — Report

Tell the user:
- Which path was chosen and why (e.g., "142 pages, no text layer → MinerU pipeline backend").
- Output location.
- How long it took.
- Any caveats (e.g., MinerU first run downloads ~5–10 GB of models).

## Manual override

If the user explicitly asks for a specific engine, skip classify and pass `--mode` directly:
- `--mode text` (force markitdown)
- `--mode image_small` (force vision read, suitable up to ~30 pages but expensive)
- `--mode image_large` (force MinerU)

Backend hint can be added with `--mineru-backend pipeline|vlm-auto-engine|hybrid-auto-engine`.

## Failure handling

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `pdftoppm: not found` | poppler missing or PATH not refreshed | Check `where pdftoppm`; user may need to restart shell |
| `mineru: not found` | venv not activated / not on PATH | `extract.py` auto-resolves via `MINERU_EXE` env var → `shutil.which("mineru")` → Windows fallback. Set `MINERU_EXE` if you used a non-default path. |
| MinerU first run hangs | Downloading 5–10 GB models | Inform user; let it run; subsequent runs are fast |
| OOM during MinerU | per-chunk RAM still too high | Re-run with smaller `--chunk-size 25` (or 10) |
| Garbled Chinese in output | Wrong language hint | Add `-l ch` (default), or `ch_lite` / `ch_server` for variants |

## Output convention

Always write the final `.md` next to the source PDF (or in the user-specified output dir). For MinerU, also keep its `images/` folder so figures render correctly when the markdown is opened.
