---
name: pdf-smart-ocr
description: Smart PDF → Markdown extractor that picks the best engine automatically. Use when the user asks to extract / OCR / convert / parse a PDF (especially scanned, image-based, or PowerPoint-export PDFs) into markdown text. Routes between markitdown (text PDFs), pdftoppm + Claude vision (image PDFs ≤50 pages), cloud VLM APIs (51–100 pages, configurable provider), and MinerU CLI (>100 pages, local). Preserves layout, tables, formulas, headings.
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
                       Input PDF
                          │
                  run classify.py
                          │
       ┌──────────────────┼──────────────────┬─────────────────────┐
       │                  │                   │                     │
   text PDF          image, ≤50 pgs      image, 51–100 pgs    image, >100 pgs
       │                  │                   │                     │
  markitdown         pdftoppm           pdftoppm + cloud         MinerU CLI
       │           + Claude vision        VLM API per page       (auto-chunked)
       │                  │                   │                     │
       └──────────────────┴───────────────────┴─────────────────────┘
                                              │
                                       Markdown output
```

## Required environment

| Tool | Purpose | Verify |
|------|---------|--------|
| `pdftoppm` (poppler) | Render PDF → PNG | `pdftoppm -v` |
| `mineru` CLI | Heavy-duty OCR/VLM parsing (image_large) | `mineru --version` |
| `markitdown` Python pkg | Fast text-PDF conversion | `python -c "import markitdown"` |
| `pdfplumber` / `pypdf` / `pypdfium2` | Text-layer probing + page count | `python -c "import pdfplumber"` |

For `image_vlm` mode, additionally **one** of these env vars (per chosen provider):

| Provider (default model) | Env var | Cost | Notes |
|---|---|---|---|
| **`mistral`** (mistral-ocr-latest) — **default** | `MISTRAL_API_KEY` | $1–2 / 1000 pages; free tier 1 RPS / 1B tok/mo | Best speed (~6 s/page) + reliable layout |
| `siliconflow` (PaddleOCR-VL-1.5) | `SILICONFLOW_API_KEY` | free (rate-limited) | ~100 s/page on free tier; **hallucinates on visually-complex PPT pages** — use only for clean text-heavy scans |
| `deepinfra` (deepseek-ai/DeepSeek-OCR) | `DEEPINFRA_API_KEY` | $0.03 in / $0.10 out per M tok | OpenAI Chat-Compat |
| `openrouter` (qwen/qwen2.5-vl-72b-instruct) | `OPENROUTER_API_KEY` | $0.25 in / $0.75 out per M tok | General-purpose VLM |

If a required tool is missing, tell the user which one and stop.

## Step-by-step procedure

### Step 1 — Classify

```bash
python "<SKILL_DIR>/scripts/classify.py" "<absolute-pdf-path>"
```

Returns a single JSON line, e.g.:

```json
{"pages": 142, "has_text_layer": false, "recommendation": "image_large"}
```

Possible recommendations: `text`, `image_small`, `image_vlm`, `image_large`.

### Step 2 — Dispatch

Choose based on `recommendation`:

#### A. `text` → markitdown

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode text --pdf "<pdf>" --out "<out_dir>"
```

Produces `<out_dir>/<pdf-stem>.md`. Fast (seconds).

#### B. `image_small` (≤50 pages) → render + Claude vision

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode image_small --pdf "<pdf>" --out "<out_dir>"
```

This only renders pages to `<out_dir>/_pages/p-001.png …`. You (Claude) MUST then:
1. Read each PNG with the `Read` tool (vision-capable).
2. Combine the visual reading into a structured markdown file at `<out_dir>/<pdf-stem>.md`.
3. Delete `<out_dir>/_pages/` after writing the .md.

Highest quality path; token cost grows linearly with pages — that's why it's bounded at ≤50.

#### C. `image_vlm` (51–100 pages) → cloud VLM API

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode image_vlm --pdf "<pdf>" --out "<out_dir>"
```

Default provider is `mistral` (fastest + reliable; free tier sufficient for personal use). Override:

```bash
# pick a different provider
python "<SKILL_DIR>/scripts/extract.py" --mode image_vlm \
    --pdf "<pdf>" --out "<out_dir>" \
    --vlm-provider mistral       # or siliconflow / deepinfra / openrouter

# pick a specific model on that provider
python "<SKILL_DIR>/scripts/extract.py" --mode image_vlm \
    --pdf "<pdf>" --out "<out_dir>" \
    --vlm-provider openrouter --vlm-model qwen/qwen2.5-vl-32b-instruct
```

Or set `PDF_SMART_OCR_VLM_PROVIDER` env var to change the default once.

If the chosen provider's env var is missing, the script fails fast with a clear message naming the var.

#### D. `image_large` (>100 pages) → MinerU (auto-chunked)

```bash
python "<SKILL_DIR>/scripts/extract.py" --mode image_large --pdf "<pdf>" --out "<out_dir>"
```

Internally:
- If pages ≤ `--chunk-size` (default 50), MinerU runs once.
- If pages > chunk-size, the PDF is processed in sequential page ranges to avoid OOM. Per-chunk outputs are merged and intermediate dirs cleaned.

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
- Any caveats (e.g., MinerU first run downloads ~1 GB of models; VLM API needs key).

## Manual override

If the user explicitly asks for a specific engine, skip classify and pass `--mode` directly:
- `--mode text` (force markitdown)
- `--mode image_small` (force Claude vision; expensive for many pages)
- `--mode image_vlm` (force cloud VLM regardless of size; pair with `--vlm-provider`)
- `--mode image_large` (force local MinerU)

## Failure handling

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `pdftoppm: not found` | poppler missing or PATH not refreshed | Check `where pdftoppm`; restart shell |
| `mineru: not found` | venv not activated / not on PATH | Set `MINERU_EXE` env var or activate venv |
| MinerU first run hangs | Downloading models (~1 GB) | Inform user; subsequent runs are fast |
| OOM during MinerU | per-chunk RAM too high | Re-run with smaller `--chunk-size 25` (or 10) |
| `Missing API key. Set environment variable XXX_API_KEY` | VLM provider key not exported | Set the named env var, retry |
| `429` from a VLM provider | rate limit hit | Switch provider (e.g., siliconflow→novita) or wait |
| Garbled Chinese in output | wrong language hint | Add `-l ch` (default), or `ch_lite` / `ch_server` |

## Output convention

Always write the final `.md` next to the source PDF (or in the user-specified output dir). For `image_large` (MinerU), also keep its `images/` folder so figures render correctly when the markdown is opened.
