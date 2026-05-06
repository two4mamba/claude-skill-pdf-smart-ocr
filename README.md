# pdf-smart-ocr

> A [Claude Code](https://docs.claude.com/claude-code) skill that converts any PDF to clean Markdown by **picking the best engine automatically**.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## What it does

Given a PDF, the skill:

1. **Classifies** the file (text PDF? image PDF? page count?)
2. **Dispatches** to the most efficient extractor:

| PDF type | Engine | Why | Speed |
|----------|--------|-----|-------|
| Text PDF (has text layer) | [`markitdown`](https://github.com/microsoft/markitdown) | No OCR needed | seconds |
| Image PDF, ≤ 10 pages | `pdftoppm` + Claude vision | Highest fidelity for short docs | seconds, modest tokens |
| Image PDF, > 10 pages | [MinerU](https://github.com/opendatalab/MinerU) CLI | Preserves headings, lists, tables, formulas, embeds figures | 3–10 s/page on CPU |

For large image PDFs, MinerU runs are **auto-chunked** to avoid OOM on 32 GB-class machines, and the resulting chunk markdowns are merged with content-hash deduplication of extracted images.

## Why not just use one tool?

- **markitdown** is fastest but blind to scans → useless for image PDFs.
- **MinerU** is excellent for image PDFs but slow & memory-heavy for trivial text PDFs.
- **Claude vision** is highest quality but expensive (token cost) for hundreds of pages.

This skill is the **dispatcher** that picks correctly per input.

## Installation

### 1. Drop into your Claude Code skills folder

```bash
git clone https://github.com/two4mamba/claude-skill-pdf-smart-ocr.git \
    ~/.claude/skills/pdf-smart-ocr
```

(Windows: `%USERPROFILE%\.claude\skills\pdf-smart-ocr`)

### 2. Install dependencies

#### Required Python packages (system Python or any env Claude can call)

```bash
pip install pdfplumber markitdown[pdf]
# pypdf or pypdfium2 also work for page counting
```

#### Required CLI tools

| Tool | Linux / macOS | Windows |
|------|---------------|---------|
| `pdftoppm` (poppler) | `brew install poppler` / `apt install poppler-utils` | `winget install oschwartz10612.Poppler` |
| `mineru` (3.1+) | `pip install -U "mineru[core]"` | Same, but consider a **short-path venv** like `C:\mineru-venv\` to avoid Windows long-path errors |

#### Pre-download MinerU models (saves time on first run)

```bash
mineru-models-download -s huggingface -m all
```

Downloads ~5–10 GB of model weights. Takes 2–5 min on a fast connection.

### 3. (Optional) Set MINERU_EXE

If `mineru` is not on your `PATH` (e.g., installed in a non-activated venv), set:

```bash
export MINERU_EXE=/path/to/your/mineru        # Linux / macOS
$env:MINERU_EXE = "C:\mineru-venv\Scripts\mineru.exe"   # Windows PowerShell
```

The skill will also auto-fallback to `C:\mineru-venv\Scripts\mineru.exe` on Windows if nothing else is found.

## Usage

### From Claude Code (the intended path)

Just say it in plain language:

```
把 D:\path\to\X.pdf 转成 markdown
OCR this scanned PDF: ~/Documents/scan.pdf
extract the contents of foo.pdf
```

Claude will pick this skill, classify, dispatch, and report the output path.

### From the CLI (without Claude)

```bash
# Auto-pick: classify and dispatch in one call
python scripts/extract.py --mode auto --pdf X.pdf --out out_dir

# Force a specific engine
python scripts/extract.py --mode text         --pdf X.pdf --out out_dir
python scripts/extract.py --mode image_small  --pdf X.pdf --out out_dir
python scripts/extract.py --mode image_large  --pdf X.pdf --out out_dir

# Tweak chunking and backend
python scripts/extract.py --mode image_large \
    --pdf X.pdf --out out_dir \
    --chunk-size 25 \
    --mineru-backend hybrid-auto-engine
```

### Classify only (diagnostic)

```bash
python scripts/classify.py X.pdf
# {"pages": 142, "avg_chars_per_page": 0.0, "has_text_layer": false, "recommendation": "image_large"}
```

## Output layout

For `image_large` mode (auto-chunked):

```
out_dir/
├── <pdf-stem>.md       # merged markdown, preserved structure
└── images/             # all extracted figures, deduplicated by content hash
```

Intermediate per-chunk dirs go under `_chunks/` and are removed unless `--keep-intermediate` is passed.

## File structure

```
pdf-smart-ocr/
├── SKILL.md              # Claude-readable spec & decision tree
├── scripts/
│   ├── classify.py       # Probe text-layer density + page count
│   └── extract.py        # Dispatcher; auto-chunking for large image PDFs
├── README.md
└── LICENSE
```

## Quality benchmark

On a 142-page Chinese PowerPoint export (no text layer):

| Engine | Total chars | Error rate (sampled) | Layout preserved | Images extracted |
|--------|------------|---------------------|-----------------|------------------|
| EasyOCR (custom pipeline) | 35,793 | 5–8% | No | 0 |
| **MinerU pipeline** (this skill) | **55,770** | **<0.5%** | **Yes** (headings, lists, quotes) | **190** |

MinerU also delivers correct Chinese punctuation and full-page coherence where EasyOCR's later pages devolve into garbage.

## Configuration knobs

| Flag | Default | Purpose |
|------|---------|---------|
| `--mode` | `auto` | Force `text` / `image_small` / `image_large` |
| `--chunk-size` | `50` | Max pages per MinerU invocation. Lower if OOM (try 25 or 10) |
| `--mineru-backend` | `pipeline` | `pipeline` (CPU) / `hybrid-auto-engine` (GPU) / `vlm-auto-engine` (GPU) |
| `--lang` | `ch` | OCR language hint (`ch`, `en`, `japan`, `korean`, etc.) |
| `--render-dpi` | `150` | DPI for `image_small` page rendering |
| `--keep-intermediate` | off | Keep `_chunks/` per-chunk outputs (debugging) |
| `MINERU_EXE` env | — | Override mineru CLI path |

## Acknowledgements

This skill is a thin dispatcher built on top of three excellent open-source projects:

- [MinerU](https://github.com/opendatalab/MinerU) by OpenDataLab — the heavy lifter for image-PDF OCR with layout preservation
- [markitdown](https://github.com/microsoft/markitdown) by Microsoft — fast text-PDF conversion
- [poppler](https://poppler.freedesktop.org/) — `pdftoppm` for rendering

## License

MIT — see [LICENSE](LICENSE).
