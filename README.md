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
| Image PDF, 51–100 pages | Cloud OCR API (configurable) | No local GPU needed; pick free or paid | 1–10 s/page |
| Image PDF, > 100 pages | [MinerU](https://github.com/opendatalab/MinerU) CLI | Preserves layout, embeds figures | 3–10 s/page on CPU |

For large image PDFs, MinerU runs are **auto-chunked** to avoid OOM on 32 GB-class machines, and the resulting chunk markdowns are merged with content-hash deduplication of extracted images.

3. **Exports** to multiple formats (`--export md,docx,pdf,layout_html,layout_pdf`):

| Format | Description | Supported by |
|--------|-------------|--------------|
| `md` | OCR markdown (default) | all engines |
| `docx` | pandoc-converted Word doc with embedded images | all engines |
| `pdf` | Word/LibreOffice-rendered PDF (md → docx → pdf) | all engines |
| **`layout_html`** | **HTML with absolute-positioned blocks reproducing the original page layout** | baidu PaddleOCR-VL, MinerU |
| **`layout_pdf`** | **PDF rendered from the layout HTML via headless Chromium** | baidu PaddleOCR-VL, MinerU |

The `layout_*` formats use bbox JSON (only baidu and MinerU emit it) to reconstruct the original page geometry — multi-column slides stay multi-column, image-on-left/text-on-right stays so. Mistral / OpenRouter / DeepInfra / Claude vision do not emit bbox, so they cannot do layout preservation.

## Cloud OCR providers (image_vlm mode)

Each provider needs its API key in an environment variable. Default is **Mistral OCR** — empirically fastest + most reliable (see [benchmark](#empirical-comparison-2026-05-06)).

| Provider | Default model | Env var(s) | Pricing | Notes |
|----------|--------------|------------|---------|-------|
| **`mistral`** (default) | `mistral-ocr-latest` | `MISTRAL_API_KEY` | $1–2 / 1000 pages; free tier 1 RPS / 1B tok / month, no credit card | ~6 s/page; reliable layout; per-image |
| **`baidu`** | `paddleocr-vl` (official) | `BAIDU_API_URL` + `BAIDU_ACCESS_TOKEN` | First 1000 pages free; ¥0.18/page pay-as-you-go; ¥0.09 at 1M-page tier | **PDF-native** (one call per PDF, synchronous); returns full layout JSON (bbox, types) — drives `--export layout_html,layout_pdf` |
| `openrouter` | `qwen/qwen2.5-vl-72b-instruct` | `OPENROUTER_API_KEY` | $0.25 in / $0.75 out per M tok | Generic VLM, useful when also doing QA |
| `deepinfra` | `deepseek-ai/DeepSeek-OCR` | `DEEPINFRA_API_KEY` | $0.03 in / $0.10 out per M tok | chat-compat fallback |
| ~~`siliconflow`~~ | `PaddleOCR-VL-1.5` (free) | `SILICONFLOW_API_KEY` | Free | **DEPRECATED** — empirically hallucinated 3/7 pages on PowerPoint exports (infinite "1. 2. 3. ..." numbered lists); ~100 s/page. Kept only for reference |

Override the default with `--vlm-provider <name>` or set `PDF_SMART_OCR_VLM_PROVIDER` env var. Override the model with `--vlm-model <name>`.

### PDF-native vs image-based providers

- **Image-based** (mistral, openrouter, deepinfra): the dispatcher renders each PDF page to PNG and calls the API per page.
- **PDF-native** (baidu): the dispatcher sends the entire PDF in one async task. The provider returns markdown + extracted images + a rich layout JSON (with bbox coordinates) saved to `<stem>.layout.json`. This layout JSON is the foundation for future layout-preserving export (.docx/.pdf).

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

### 3. Configure API keys for cloud VLM mode

Cloud VLM mode (`image_vlm`) needs **one** API key per provider you intend to use. Default is **Mistral**, so set `MISTRAL_API_KEY` first.

#### Where to get a key

| Provider | Sign-up + key page |
|----------|-------------------|
| Mistral (default) | https://console.mistral.ai/api-keys — phone verification only, no credit card; 1B tokens/month free |
| Baidu | https://aistudio.baidu.com/paddleocr — log in, open the **PaddleOCR-VL** demo page; the page shows your personal **API URL** (ends with `/layout-parsing`) and **Access Token**. Copy both. The token expires every 30 days. |
| OpenRouter | https://openrouter.ai/keys — credit-based |
| DeepInfra | https://deepinfra.com/dash/api_keys — pay-as-you-go |
| ~~SiliconFlow~~ | https://cloud.siliconflow.cn/account/ak — DEPRECATED, see provider table |

#### Set the key (persists across sessions)

**Windows (PowerShell)** — recommended on Windows because it writes to the user registry, persisting across all future shells (and Claude Code restarts):

```powershell
[Environment]::SetEnvironmentVariable('MISTRAL_API_KEY', 'your-actual-key-here', 'User')

# Baidu (AI Studio) needs the personal API URL + access token from
# https://aistudio.baidu.com/paddleocr
[Environment]::SetEnvironmentVariable('BAIDU_API_URL',     'https://xxxxx.aistudio-app.com/layout-parsing', 'User')
[Environment]::SetEnvironmentVariable('BAIDU_ACCESS_TOKEN','your-access-token-from-the-page',              'User')

# Other providers:
[Environment]::SetEnvironmentVariable('OPENROUTER_API_KEY', 'sk-or-...', 'User')
[Environment]::SetEnvironmentVariable('DEEPINFRA_API_KEY', '...', 'User')

# Verify (returns the value length; secrets are never printed):
([Environment]::GetEnvironmentVariable('MISTRAL_API_KEY','User')).Length
```

**Important**: existing PowerShell / Claude Code windows will NOT see the new value until restarted. Open a fresh shell, or for the current session also do `$env:MISTRAL_API_KEY = 'your-key'`.

**Windows (cmd.exe)** — `setx` writes to the user registry the same way:

```cmd
setx MISTRAL_API_KEY      "your-actual-key-here"
setx BAIDU_API_URL        "https://xxxxx.aistudio-app.com/layout-parsing"
setx BAIDU_ACCESS_TOKEN   "your-access-token-from-the-page"
```

**macOS / Linux (bash / zsh)** — append to your shell profile so it loads on every new shell:

```bash
# bash → ~/.bashrc, zsh → ~/.zshrc
echo 'export MISTRAL_API_KEY="your-actual-key-here"' >> ~/.zshrc
echo 'export BAIDU_API_URL="https://xxxxx.aistudio-app.com/layout-parsing"' >> ~/.zshrc
echo 'export BAIDU_ACCESS_TOKEN="your-access-token-from-the-page"' >> ~/.zshrc
source ~/.zshrc
```

**Baidu token expiry**: the AI Studio access token rotates roughly every 30 days. When it expires you'll see `error code 110 "Access token invalid"`. Go back to https://aistudio.baidu.com/paddleocr, copy the new token, and re-run the `SetEnvironmentVariable` / `setx` / `export` line above.

#### Important: do NOT put keys in source code

This skill is open source. The `env_var` field in each `providers/*.py` file is the **name** of the env var (e.g., `"MISTRAL_API_KEY"`), not the key itself. The key lives only in your OS environment, never in this repo.

If you accidentally commit a real key, rotate it immediately at the provider's dashboard.

### 4. (Optional) Set MINERU_EXE

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

# Multi-format export: get .md AND .docx AND .pdf in one shot
python scripts/extract.py --mode auto --pdf X.pdf --out out_dir \
    --export md,docx,pdf
```

### Multi-format export

By default the skill produces only `<stem>.md`. Add formats with `--export`:

```bash
# Markdown only (default)
python scripts/extract.py --mode auto --pdf X.pdf --out out_dir

# Markdown + Word
python scripts/extract.py --mode auto --pdf X.pdf --out out_dir --export md,docx

# All five formats (only with baidu or MinerU; layout_* require bbox)
python scripts/extract.py --mode image_vlm --vlm-provider baidu \
    --pdf X.pdf --out out_dir --export md,docx,pdf,layout_html,layout_pdf
```

Two conversion pipelines run in parallel after the OCR step:

```
                       ┌─ md → md (OCR output)
                       │
                       ├─ md ──pandoc──► docx ──Word/LibreOffice──► pdf
OCR engine ─────────►──┤
                       │
                       └─ layout JSON ──adapter──► NormalizedLayout
                                                     │
                                                     ├──► layout.html
                                                     └──► layout.pdf  (Chromium)
```

Required dependencies for each format:

| Format | Extra dependency | How to install |
|--------|-----------------|----------------|
| md | (built-in) | — |
| docx | `pandoc` CLI | `winget install JohnMacFarlane.Pandoc` |
| pdf | `pandoc` + Word (Windows) or LibreOffice (macOS/Linux) | Windows: `pip install docx2pdf` (drives MS Word via COM). macOS/Linux: install LibreOffice (`brew install --cask libreoffice`, `apt install libreoffice`, etc.) — the skill auto-routes to `soffice --convert-to pdf`. |
| layout_html | `playwright` (only used for layout_pdf, optional) | — |
| layout_pdf | `playwright` + Chromium | Auto-installed on first use (`pip install playwright && playwright install chromium`) |

### Two flavors of `.pdf` output explained

| File | How it's made | Quality |
|------|---------------|---------|
| `<stem>.pdf` | md → docx → Word renders to PDF | Logical structure (headings, lists, tables, embedded figures) but **single-column linear flow** — loses the original PPT's two-column or grid layout |
| `<stem>.layout.pdf` | bbox JSON → HTML with `position:absolute` → Chromium prints PDF | **Reproduces original page geometry** — multi-column slides stay multi-column, figure-left/text-right stays so. Doesn't preserve original colors / fonts |

Pick `pdf` for editable / readable outputs; pick `layout.pdf` for "looks like the original slide" archival. Often you want both.

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
│   ├── extract.py                 # Dispatcher; auto-chunking; layout export router
│   ├── convert.py                 # md → docx (pandoc) → pdf (Word on Win / LibreOffice on macOS+Linux)
│   ├── providers/                 # Cloud OCR provider implementations
│   │   ├── __init__.py            # Registry: get_provider(name) → instance
│   │   ├── base.py                # VLMProvider ABC + helpers (image + pdf APIs)
│   │   ├── mistral.py             # default: Mistral OCR (per-image /v1/ocr)
│   │   ├── baidu.py               # PDF-native: official PaddleOCR-VL + layout JSON
│   │   ├── openrouter.py          # Qwen2.5-VL-72B-Instruct
│   │   ├── deepinfra.py           # DeepSeek-OCR (chat-compat fallback)
│   │   └── siliconflow.py         # DEPRECATED (hallucinates)
│   └── layout/                    # Layout-preserving export pipeline
│       ├── normalized.py          # NormalizedLayout dataclass — engine-agnostic IR
│       ├── adapters/
│       │   ├── baidu.py           # baidu layout.json → NormalizedLayout
│       │   └── mineru.py          # MinerU middle.json → NormalizedLayout
│       ├── render_html.py         # NormalizedLayout → absolute-positioned HTML
│       ├── render_pdf.py          # HTML → PDF via Playwright Chromium
│       └── templates/page.css     # Base styles (CJK fonts, page geometry)
├── README.md
└── LICENSE
```

The `layout/` module is engine-agnostic: any OCR engine that emits bbox JSON
just needs an adapter that produces `NormalizedLayout`, and both renderers
work without changes.

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
| `--vlm-provider` | `mistral` | `mistral` / `baidu` / `openrouter` / `deepinfra` / `siliconflow` (deprecated) |
| `--vlm-model` | (provider default) | Override the default model name |
| `--export` | `md` | Comma-separated formats: `md`, `docx`, `pdf`, `layout_html`, `layout_pdf` |
| `--chunk-size` | `50` | Max pages per MinerU invocation. Lower if OOM (try 25 or 10) |
| `--mineru-backend` | `pipeline` | `pipeline` (CPU) / `hybrid-auto-engine` (GPU) / `vlm-auto-engine` (GPU) |
| `--lang` | `ch` | OCR language hint (`ch`, `en`, `japan`, `korean`, etc.) |
| `--render-dpi` | `150` | DPI for `image_small` / `image_vlm` page rendering |
| `--keep-intermediate` | off | Keep `_chunks/` per-chunk outputs (debugging) |
| `MINERU_EXE` env | — | Override mineru CLI path |
| `PDF_SMART_OCR_VLM_PROVIDER` env | — | Override default VLM provider |
| `<PROVIDER>_API_KEY` env | — | API key per provider (see table above) |
| `BAIDU_API_URL` + `BAIDU_ACCESS_TOKEN` env | — | Baidu AI Studio personal endpoint + token (from https://aistudio.baidu.com/paddleocr) |

## Acknowledgements

This skill is a thin dispatcher built on top of three excellent open-source projects:

- [MinerU](https://github.com/opendatalab/MinerU) by OpenDataLab — the heavy lifter for image-PDF OCR with layout preservation
- [markitdown](https://github.com/microsoft/markitdown) by Microsoft — fast text-PDF conversion
- [poppler](https://poppler.freedesktop.org/) — `pdftoppm` for rendering

## License

MIT — see [LICENSE](LICENSE).
