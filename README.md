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
| Image PDF, ≤ 50 pages | `pdftoppm` + Claude vision | Highest fidelity for short docs | seconds, modest tokens |
| Image PDF, 51–100 pages | Cloud VLM API (configurable) | No local GPU needed; pick free or paid | 1–3 s/page |
| Image PDF, > 100 pages | [MinerU](https://github.com/opendatalab/MinerU) CLI | Preserves layout, embeds figures | 3–10 s/page on CPU |

For large image PDFs, MinerU runs are **auto-chunked** to avoid OOM on 32 GB-class machines, and the resulting chunk markdowns are merged with content-hash deduplication of extracted images.

## Cloud VLM providers (image_vlm mode)

Each provider needs its API key in an environment variable. Default is **Mistral OCR** — empirically fastest + most reliable (see [benchmark](#empirical-comparison-2026-05-06)).

| Provider | Default model | Env var | Pricing | Notes |
|----------|--------------|---------|---------|-------|
| **`mistral`** (default) | `mistral-ocr-latest` | `MISTRAL_API_KEY` | $1–2 / 1000 pages; free tier 1 RPS / 1B tok / month, no credit card | ~6 s/page; reliable layout |
| `siliconflow` | `PaddlePaddle/PaddleOCR-VL-1.5` | `SILICONFLOW_API_KEY` | **Free** (rate-limited; ~50 RPD without paid history) | ~100 s/page on free tier; hallucinates on visually-complex pages — use for clean text scans only |
| `deepinfra` | `deepseek-ai/DeepSeek-OCR` | `DEEPINFRA_API_KEY` | $0.03 in / $0.10 out per M tok | |
| `openrouter` | `qwen/qwen2.5-vl-72b-instruct` | `OPENROUTER_API_KEY` | $0.25 in / $0.75 out per M tok | |

Override the default with `--vlm-provider <name>` or set `PDF_SMART_OCR_VLM_PROVIDER` env var. Override the model with `--vlm-model <name>`.

### Empirical comparison (2026-05-06)

10-page Chinese PowerPoint export (image PDF, no text layer) — image-heavy slide deck:

| Provider | Total time | Per page | Failed pages | Notes |
|----------|-----------|---------|-------------|-------|
| **Mistral OCR** | **58.6 s** | **5.9 s** | 0/10 | Headings, lists, bilingual text all preserved |
| SiliconFlow PaddleOCR-VL-1.5 (free) | 711 s (7 pages) | 101.6 s | 3/7 (43%) | On visually-complex pages, model degenerates and emits `1. 1. 2. 3. ... 840.` until max_tokens |

**Conclusion**: For PowerPoint-export PDFs (which mix dense visuals with sparse text), Mistral wins on every dimension. SiliconFlow PaddleOCR-VL-1.5 is only acceptable for clean text-heavy scans.

> ⚠ **Important**: `Qwen2.5-72B-Instruct` (no `-VL`) is **text-only and cannot OCR**. The default for OpenRouter is the vision variant `qwen/qwen2.5-vl-72b-instruct`.

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
python scripts/extract.py --mode image_vlm    --pdf X.pdf --out out_dir
python scripts/extract.py --mode image_large  --pdf X.pdf --out out_dir

# Cloud VLM mode — pick a provider/model
python scripts/extract.py --mode image_vlm \
    --pdf X.pdf --out out_dir \
    --vlm-provider mistral

python scripts/extract.py --mode image_vlm \
    --pdf X.pdf --out out_dir \
    --vlm-provider openrouter \
    --vlm-model qwen/qwen2.5-vl-32b-instruct

# MinerU — tweak chunking and backend
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
├── SKILL.md                       # Claude-readable spec & decision tree
├── scripts/
│   ├── classify.py                # Probe text-layer density + page count
│   ├── extract.py                 # Dispatcher; auto-chunking for image_large
│   └── providers/                 # VLM cloud provider implementations
│       ├── __init__.py            # Registry: get_provider(name) → instance
│       ├── base.py                # VLMProvider ABC + chat-completions helper
│       ├── siliconflow.py         # Free PaddleOCR-VL-1.5
│       ├── mistral.py             # Mistral OCR (dedicated /v1/ocr endpoint)
│       ├── deepinfra.py           # DeepSeek-OCR
│       └── openrouter.py          # Qwen2.5-VL-72B-Instruct
├── README.md
└── LICENSE
```

### Adding a new VLM provider

1. Create `scripts/providers/<name>.py` subclassing `VLMProvider`
2. Set the four class attrs: `name`, `default_model`, `env_var`, `endpoint`
3. Implement `ocr_image(self, image_bytes, *, model=None, lang="ch") -> str`
4. Register the class in `scripts/providers/__init__.py`'s `PROVIDERS` dict

For OpenAI Chat-Compat APIs you can usually delegate to `self._chat_completion_image(...)`. See `siliconflow.py` for a 10-line example.

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
| `--mode` | `auto` | Force `text` / `image_small` / `image_vlm` / `image_large` |
| `--vlm-provider` | `siliconflow` | `siliconflow` / `mistral` / `deepinfra` / `openrouter` |
| `--vlm-model` | (provider default) | Override the default model name |
| `--chunk-size` | `50` | Max pages per MinerU invocation. Lower if OOM (try 25 or 10) |
| `--mineru-backend` | `pipeline` | `pipeline` (CPU) / `hybrid-auto-engine` (GPU) / `vlm-auto-engine` (GPU) |
| `--lang` | `ch` | OCR language hint (`ch`, `en`, `japan`, `korean`, etc.) |
| `--render-dpi` | `150` | DPI for `image_small` / `image_vlm` page rendering |
| `--keep-intermediate` | off | Keep `_chunks/` per-chunk outputs (debugging) |
| `MINERU_EXE` env | — | Override mineru CLI path |
| `PDF_SMART_OCR_VLM_PROVIDER` env | — | Override default VLM provider |
| `<PROVIDER>_API_KEY` env | — | API key per provider (see table above) |

## Acknowledgements

This skill is a thin dispatcher built on top of three excellent open-source projects:

- [MinerU](https://github.com/opendatalab/MinerU) by OpenDataLab — the heavy lifter for image-PDF OCR with layout preservation
- [markitdown](https://github.com/microsoft/markitdown) by Microsoft — fast text-PDF conversion
- [poppler](https://poppler.freedesktop.org/) — `pdftoppm` for rendering

## License

MIT — see [LICENSE](LICENSE).
