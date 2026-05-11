"""Dispatch a PDF to the right extraction engine and produce Markdown.

Usage:
    python extract.py --mode {auto|text|image_small|image_vlm|image_large} \
                      --pdf <pdf_path> --out <out_dir> \
                      [--vlm-provider {siliconflow|mistral|deepinfra|openrouter}] \
                      [--vlm-model <model_name>] \
                      [--mineru-backend pipeline|vlm-auto-engine|hybrid-auto-engine] \
                      [--lang ch] [--render-dpi 150] \
                      [--chunk-size 50] [--keep-intermediate]

Modes:
    auto         Run classify, then dispatch.
    text         markitdown (fast, text-layer PDFs).
    image_small  Render pages to PNG; caller (Claude) reads them with vision (≤50 pages).
    image_vlm    Render + send each page to a cloud VLM API (51–100 pages typical).
    image_large  Local MinerU CLI (>100 pages typical, auto-chunks to avoid OOM).

For `image_small`, this script only renders. The caller must read the PNGs and
write the final .md.

For `image_vlm`:
- Renders each page, calls the chosen VLM provider per page, concatenates Markdown.
- Default provider: siliconflow (free PaddleOCR-VL-1.5). Override with --vlm-provider.
- Each provider needs an API key in its env var (see providers/*.py).

For `image_large`:
- If pages <= chunk-size, MinerU runs once.
- Else, the PDF is processed in sequential page ranges, then merged. Per-chunk
  outputs go under <out_dir>/_chunks/ and are removed unless --keep-intermediate
  is set. Final result: <out_dir>/<pdf_stem>.md  +  <out_dir>/images/
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _resolve_mineru_exe() -> str:
    """Find the mineru executable. Priority:
    1. MINERU_EXE env var (override)
    2. PATH lookup (works if Python venv is activated, or mineru installed system-wide)
    3. Common Windows fallback (short-path venv)
    """
    if env := os.environ.get("MINERU_EXE"):
        return env
    found = shutil.which("mineru") or shutil.which("mineru.exe")
    if found:
        return found
    # Windows convention: short-path venv to avoid long-path limit
    fallback = r"C:\mineru-venv\Scripts\mineru.exe"
    if Path(fallback).exists():
        return fallback
    return "mineru"  # last resort, will fail with informative error if missing


MINERU_EXE = _resolve_mineru_exe()


def log(msg: str):
    print(f"[pdf-smart-ocr] {msg}", flush=True)


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def page_count(pdf: Path) -> int:
    """Cheap page count, robust across environments."""
    for tryer in (
        lambda: __import__("pypdf").PdfReader(str(pdf)).pages,
        lambda: __import__("pypdfium2").PdfDocument(str(pdf)),
        lambda: __import__("pdfplumber").open(str(pdf)).pages,
    ):
        try:
            return len(tryer())
        except Exception:
            continue
    raise SystemExit("Need one of: pypdf / pypdfium2 / pdfplumber to count PDF pages.")


def run_classify(pdf: Path) -> dict:
    here = Path(__file__).parent
    classifier = here / "classify.py"
    out = subprocess.check_output([sys.executable, str(classifier), str(pdf)], text=True, encoding="utf-8")
    return json.loads(out.strip())


def run_text(pdf: Path, out_dir: Path) -> Path:
    """Use markitdown to extract text-PDF content."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise SystemExit("markitdown not installed. Run: pip install markitdown[pdf]")

    md = MarkItDown()
    result = md.convert(str(pdf))
    out_md = out_dir / f"{pdf.stem}.md"
    out_md.write_text(result.text_content, encoding="utf-8")
    return out_md


def run_image_small(pdf: Path, out_dir: Path, dpi: int = 150) -> Path:
    """Render pages to PNG. Caller must do the visual reading."""
    pages_dir = ensure_dir(out_dir / "_pages")
    cmd = ["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(pages_dir / "p")]
    subprocess.check_call(cmd)
    return pages_dir


def run_image_vlm(
    pdf: Path,
    out_dir: Path,
    provider_name: str,
    model: str | None = None,
    lang: str = "ch",
    dpi: int = 150,
) -> Path:
    """Send the PDF to a cloud OCR provider.

    Two paths:
    - Provider with `supports_pdf=True` (e.g. baidu): submit whole PDF in one call,
      get back markdown + images + layout JSON.
    - Image-based provider (e.g. mistral): render to PNG per page, loop.
    """
    # Local import: providers package may pull urllib etc; defer until this path runs.
    sys.path.insert(0, str(Path(__file__).parent))
    from providers import get_provider

    provider = get_provider(provider_name)
    log(f"OCR provider: {provider.name}, model: {model or provider.default_model}")

    asset_root = out_dir / f"{pdf.stem}.assets"
    asset_dirname = asset_root.name

    if provider.supports_pdf:
        # ---- PDF-native path (Baidu) ----
        log(f"  submitting whole PDF to {provider.name}…")
        pdf_bytes = pdf.read_bytes()
        try:
            md, images, layout_json = provider.ocr_pdf(
                pdf_bytes, pdf.name, model=model, lang=lang
            )
        except Exception as e:
            raise SystemExit(f"OCR provider {provider.name} failed: {e}")

        # Save extracted images and rewrite markdown to point into the assets dir.
        # Provider returns markdown with bare filenames; we prepend the asset
        # dirname so the .md renders correctly from out_dir. PaddleOCR-VL uses
        # both <img src="..."> HTML and ![](...) markdown forms, so we patch the
        # bare path text directly to cover both.
        if images:
            asset_root.mkdir(exist_ok=True)
            for fname, raw in images.items():
                (asset_root / fname).write_bytes(raw)
                md = md.replace(fname, f"{asset_dirname}/{fname}")
            log(f"saved {len(images)} extracted figures → {asset_root}")

        # Save layout JSON for downstream layout-preserving export (.docx/.pdf)
        if layout_json is not None:
            layout_path = out_dir / f"{pdf.stem}.layout.json"
            layout_path.write_text(
                json.dumps(layout_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log(f"saved layout JSON → {layout_path}")

        final_md = out_dir / f"{pdf.stem}.md"
        final_md.write_text(md, encoding="utf-8")
        return final_md

    # ---- Image-based path (Mistral, OpenRouter, etc.) ----
    pages_dir = ensure_dir(out_dir / "_pages")
    cmd = ["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(pages_dir / "p")]
    subprocess.check_call(cmd)
    pngs = sorted(pages_dir.glob("p-*.png"))
    if not pngs:
        raise SystemExit(f"No pages rendered to {pages_dir}")

    md_parts: list[str] = []
    total_images = 0

    for i, png in enumerate(pngs, 1):
        log(f"  page {i}/{len(pngs)}: {png.name}")
        img_bytes = png.read_bytes()
        try:
            md, images = provider.ocr_image(img_bytes, model=model, lang=lang)
        except Exception as e:
            raise SystemExit(f"OCR provider {provider.name} failed on page {i}: {e}")

        # Save extracted figures and rewrite markdown image refs to relative paths.
        # Per-page prefix avoids cross-page filename collisions (e.g., img-0.jpeg from
        # page 2 vs page 5).
        if images:
            asset_root.mkdir(exist_ok=True)
            for img_id, raw in images.items():
                new_name = f"page-{i:03d}-{img_id}"
                (asset_root / new_name).write_bytes(raw)
                # rewrite both forms: `![alt](id)` and `![alt](id "title")`
                md = md.replace(f"]({img_id})", f"]({asset_dirname}/{new_name})")
                md = md.replace(f"]({img_id} ", f"]({asset_dirname}/{new_name} ")
                total_images += 1

        md_parts.append(md.strip())

    final_md = out_dir / f"{pdf.stem}.md"
    final_md.write_text("\n\n---\n\n".join(md_parts), encoding="utf-8")
    if total_images:
        log(f"saved {total_images} extracted figures → {asset_root}")

    # Clean rendered pages (we keep only the markdown + assets)
    shutil.rmtree(pages_dir, ignore_errors=True)
    return final_md


def _mineru_run(pdf: Path, dest: Path, backend: str, lang: str, start: int, end: int) -> Path:
    """Run a single MinerU invocation on [start, end] (0-indexed, inclusive). Returns the .md path."""
    # Verify mineru is callable (the resolver above may have returned a literal "mineru"
    # that's not actually on PATH).
    if MINERU_EXE not in ("mineru", "mineru.exe") and not Path(MINERU_EXE).exists():
        raise SystemExit(
            f"mineru CLI not found at {MINERU_EXE}. "
            "Set MINERU_EXE env var, or install mineru and put it on PATH."
        )
    ensure_dir(dest)
    cmd = [
        MINERU_EXE,
        "-p", str(pdf),
        "-o", str(dest),
        "-b", backend,
        "-s", str(start),
        "-e", str(end),
    ]
    if backend.startswith(("pipeline", "hybrid")):
        cmd.extend(["-l", lang])
    log(f"  mineru -s {start} -e {end}  →  {dest.name}")
    subprocess.check_call(cmd)

    candidates = list(dest.glob(f"{pdf.stem}/*/{pdf.stem}.md"))
    if not candidates:
        candidates = list(dest.rglob("*.md"))
    if not candidates:
        raise SystemExit(f"MinerU finished but no .md found under {dest}")
    return candidates[0]


def _merge_chunks(chunk_md_paths: list[Path], final_md: Path, final_img_dir: Path) -> dict:
    """Concatenate chunk markdowns and dedupe images by filename (content-hash).

    Also merges per-chunk MinerU middle.json files into a single top-level
    `<final_md.stem>.middle.json` (when every chunk has one). Pages are
    renumbered sequentially across chunks so the merged JSON looks like a
    single-shot run. The layout export step picks it up via the alternate
    location (top-level), keeping the flat per-chunk images dir intact.
    """
    ensure_dir(final_img_dir)
    seen_imgs = 0
    md_parts = []
    middle_pages: list[dict] = []
    have_all_middles = True
    for md in chunk_md_paths:
        md_parts.append(md.read_text(encoding="utf-8"))
        chunk_imgs = md.parent / "images"
        if chunk_imgs.exists():
            for img in chunk_imgs.iterdir():
                dst = final_img_dir / img.name
                if not dst.exists():
                    shutil.copy2(img, dst)
                    seen_imgs += 1
        # Per-chunk middle.json sits next to the chunk .md
        chunk_middle = md.parent / f"{md.stem}_middle.json"
        if chunk_middle.exists() and have_all_middles:
            try:
                raw = json.loads(chunk_middle.read_text(encoding="utf-8"))
                for page in raw.get("pdf_info", []):
                    page["page_idx"] = len(middle_pages)  # global sequential
                    middle_pages.append(page)
            except (OSError, json.JSONDecodeError):
                have_all_middles = False
        else:
            have_all_middles = False
    final_md.write_text("\n\n".join(md_parts), encoding="utf-8")

    info = {"chunks": len(chunk_md_paths), "images": seen_imgs, "middle_pages": 0}
    if have_all_middles and middle_pages:
        merged_middle = final_md.with_suffix(".middle.json")
        merged_middle.write_text(
            json.dumps({"pdf_info": middle_pages}, ensure_ascii=False),
            encoding="utf-8",
        )
        info["middle_pages"] = len(middle_pages)
    return info


def run_image_large(
    pdf: Path,
    out_dir: Path,
    backend: str = "pipeline",
    lang: str = "ch",
    chunk_size: int = 50,
    keep_intermediate: bool = False,
) -> Path:
    """Run MinerU. Auto-chunk if PDF exceeds chunk_size pages."""
    pages = page_count(pdf)
    log(f"PDF has {pages} pages, chunk_size={chunk_size}")

    if pages <= chunk_size:
        # Single-shot
        final_md = _mineru_run(pdf, out_dir, backend, lang, start=0, end=pages - 1)
        log(f"single-shot output: {final_md}")
        return final_md

    # Chunked path
    chunks_root = ensure_dir(out_dir / "_chunks")
    ranges = []
    for start in range(0, pages, chunk_size):
        end = min(start + chunk_size - 1, pages - 1)
        ranges.append((start, end))
    log(f"splitting into {len(ranges)} chunks: {ranges}")

    chunk_mds: list[Path] = []
    for i, (start, end) in enumerate(ranges, 1):
        dest = chunks_root / f"chunk_{i:03d}_{start}-{end}"
        try:
            chunk_mds.append(_mineru_run(pdf, dest, backend, lang, start, end))
        except subprocess.CalledProcessError as e:
            raise SystemExit(
                f"Chunk {i} ({start}-{end}) failed (exit {e.returncode}). "
                f"Try smaller --chunk-size."
            )

    # Merge
    final_md = out_dir / f"{pdf.stem}.md"
    final_imgs = out_dir / "images"
    info = _merge_chunks(chunk_mds, final_md, final_imgs)
    log(f"merged {info['chunks']} chunks, {info['images']} unique images → {final_md}")

    if not keep_intermediate:
        shutil.rmtree(chunks_root, ignore_errors=True)
        log(f"cleaned intermediate: {chunks_root}")

    return final_md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "text", "image_small", "image_vlm", "image_large"],
                    default="auto")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mineru-backend", default="pipeline")
    ap.add_argument("--lang", default="ch")
    ap.add_argument("--render-dpi", type=int, default=150)
    ap.add_argument("--chunk-size", type=int, default=50,
                    help="Max pages per MinerU invocation (default 50). Lower if you OOM.")
    ap.add_argument("--keep-intermediate", action="store_true",
                    help="Keep per-chunk dirs under <out>/_chunks (debugging).")
    ap.add_argument("--vlm-provider",
                    choices=["mistral", "baidu", "openrouter", "deepinfra", "siliconflow"],
                    default=os.environ.get("PDF_SMART_OCR_VLM_PROVIDER", "mistral"),
                    help="Cloud OCR provider for image_vlm mode. "
                         "default=mistral (fastest, $1-2/1k pages); "
                         "baidu (official PaddleOCR-VL, first 1000 pages free, "
                         "PDF-native + layout JSON); openrouter (generic VLM); "
                         "siliconflow is DEPRECATED (hallucinates).")
    ap.add_argument("--vlm-model", default=None,
                    help="Override the default model name for the chosen provider.")
    ap.add_argument("--export", default="md",
                    help="Comma-separated output formats: md,docx,pdf,layout_html,layout_pdf. "
                         "Default: md only. layout_* preserves the original page layout "
                         "(only supported with --vlm-provider baidu OR --mode image_large).")
    args = ap.parse_args()

    pdf = Path(args.pdf).resolve()
    out_dir = ensure_dir(Path(args.out).resolve())

    if not pdf.exists():
        raise SystemExit(f"PDF not found: {pdf}")

    mode = args.mode
    if mode == "auto":
        info = run_classify(pdf)
        log(f"classify: {info}")
        mode = info["recommendation"]
        log(f"auto-selected mode: {mode}")

    # Parse + validate --export
    export_formats = [f.strip().lower() for f in args.export.split(",") if f.strip()]
    valid = {"md", "docx", "pdf", "layout_html", "layout_pdf"}
    invalid = [f for f in export_formats if f not in valid]
    if invalid:
        raise SystemExit(
            f"--export got unknown format(s): {invalid}. "
            f"Use any of: md, docx, pdf, layout_html, layout_pdf."
        )

    # image_small only renders PNGs for the caller (Claude) to read with
    # vision — it does NOT produce a .md, so any conversion-based export
    # (docx, pdf, layout_*) cannot be fulfilled. Reject up front so callers
    # don't think the export succeeded silently.
    if mode == "image_small":
        non_md = [f for f in export_formats if f != "md"]
        if non_md:
            raise SystemExit(
                f"--export {non_md} is not supported with --mode image_small. "
                f"This mode only renders pages to PNGs for downstream vision; "
                f"no .md is produced, so there is nothing to convert. Use --mode "
                f"image_vlm or image_large if you need docx/pdf/layout output."
            )

    # Layout-preserving export requires bbox data — only baidu (image_vlm) and
    # MinerU (image_large) provide it. Mistral / image_small / text don't.
    needs_layout = any(f in export_formats for f in ("layout_html", "layout_pdf"))
    if needs_layout:
        if mode in ("text", "image_small"):
            raise SystemExit(
                f"--export layout_html/layout_pdf is not supported with --mode {mode}. "
                f"Layout preservation requires bbox data, which is only produced by "
                f"--mode image_large (MinerU) or --mode image_vlm --vlm-provider baidu."
            )
        if mode == "image_vlm" and args.vlm_provider != "baidu":
            raise SystemExit(
                f"--export layout_html/layout_pdf with --mode image_vlm requires "
                f"--vlm-provider baidu (got: {args.vlm_provider}). Mistral/OpenRouter/"
                f"DeepInfra don't return bbox coordinates."
            )

    t0 = time.time()
    md_path: Path | None = None
    summary: dict = {"mode": mode}

    if mode == "text":
        md_path = run_text(pdf, out_dir)
        log(f"DONE (markitdown) → {md_path}  ({time.time()-t0:.1f}s)")
    elif mode == "image_small":
        pages_dir = run_image_small(pdf, out_dir, dpi=args.render_dpi)
        log(f"DONE (rendered to PNGs) → {pages_dir}  ({time.time()-t0:.1f}s)")
        # No .md is produced in this mode (Claude must read the PNGs).
        summary.update({
            "pages_dir": str(pages_dir),
            "next_step": "Caller MUST read each PNG with vision and write the final .md",
        })
    elif mode == "image_vlm":
        md_path = run_image_vlm(
            pdf, out_dir,
            provider_name=args.vlm_provider,
            model=args.vlm_model,
            lang=args.lang,
            dpi=args.render_dpi,
        )
        log(f"DONE ({args.vlm_provider}) → {md_path}  ({time.time()-t0:.1f}s)")
        summary.update({"provider": args.vlm_provider, "model": args.vlm_model})
    elif mode == "image_large":
        md_path = run_image_large(
            pdf, out_dir,
            backend=args.mineru_backend,
            lang=args.lang,
            chunk_size=args.chunk_size,
            keep_intermediate=args.keep_intermediate,
        )
        log(f"DONE (MinerU) → {md_path}  ({time.time()-t0:.1f}s)")
    else:
        raise SystemExit(f"Unknown mode: {mode}")

    # Optional: convert to additional formats. Only runs if we produced an .md
    # AND the user asked for more than 'md'. Layout exports are handled
    # separately below since they don't go through pandoc.
    # NB: a SystemExit here propagates — the user explicitly requested these
    # formats, so a missing pandoc / docx2pdf is a hard failure, not a warning.
    extra_formats = [f for f in export_formats if f not in ("md", "layout_html", "layout_pdf")]
    if md_path and extra_formats:
        from convert import export as _export
        outputs = _export(md_path, extra_formats, out_dir=md_path.parent)
        for fmt, p in outputs.items():
            if fmt != "md":
                log(f"exported {fmt}: {p}")
        summary["exports"] = {f: str(p) for f, p in outputs.items()}

    # Optional: layout-preserving export. Independent of pandoc pipeline —
    # uses bbox JSON saved by the OCR step (baidu's layout.json, MinerU's
    # middle.json) to render absolute-positioned HTML and PDF.
    # Failures here also propagate — user asked for layout_*, give them
    # a non-zero exit so they don't think it was produced.
    if needs_layout:
        layout_outputs = _run_layout_export(
            mode=mode,
            provider=args.vlm_provider if mode == "image_vlm" else None,
            pdf=pdf,
            out_dir=out_dir,
            want_html="layout_html" in export_formats,
            want_pdf="layout_pdf" in export_formats,
        )
        for fmt, p in layout_outputs.items():
            log(f"exported {fmt}: {p}")
        summary.setdefault("exports", {}).update({k: str(v) for k, v in layout_outputs.items()})

    if md_path is not None:
        summary["output"] = str(md_path)
    print(json.dumps(summary, ensure_ascii=False))


def _run_layout_export(
    *,
    mode: str,
    provider: str | None,
    pdf: Path,
    out_dir: Path,
    want_html: bool,
    want_pdf: bool,
) -> dict[str, Path]:
    """Adapt the OCR engine's layout JSON → NormalizedLayout → HTML (and PDF)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from layout import NormalizedLayout
    from layout.adapters import adapt_baidu, adapt_mineru
    from layout.render_html import render_html
    from layout.render_pdf import render_pdf

    layout: NormalizedLayout
    out_html: Path

    if mode == "image_vlm" and provider == "baidu":
        layout_json = out_dir / f"{pdf.stem}.layout.json"
        if not layout_json.exists():
            raise SystemExit(
                f"baidu layout.json not found at {layout_json}. "
                f"Did the baidu OCR step succeed?"
            )
        asset_root = out_dir / f"{pdf.stem}.assets"
        layout = adapt_baidu(layout_json, str(asset_root), pdf.name)
        out_html = out_dir / f"{pdf.stem}.layout.html"

    elif mode == "image_large":
        # MinerU lays out outputs differently for single-shot vs chunked runs:
        #   single-shot: <out>/<stem>/auto/<stem>_middle.json  +  auto/images/
        #   chunked    : <out>/<stem>.middle.json (merged)     +  <out>/images/
        single_middle = out_dir / pdf.stem / "auto" / f"{pdf.stem}_middle.json"
        merged_middle = out_dir / f"{pdf.stem}.middle.json"
        if single_middle.exists():
            middle = single_middle
            asset_root = single_middle.parent / "images"
            url_prefix = f"{pdf.stem}/auto/images"
        elif merged_middle.exists():
            middle = merged_middle
            asset_root = out_dir / "images"
            url_prefix = "images"
        else:
            raise SystemExit(
                f"MinerU middle.json not found at {single_middle} or "
                f"{merged_middle}. Layout export needs either a single-shot "
                f"run or chunks that all produced middle.json. Try a larger "
                f"--chunk-size, or omit --export layout_*."
            )
        layout = adapt_mineru(
            middle, str(asset_root), pdf.name, asset_url_prefix=url_prefix
        )
        out_html = out_dir / f"{pdf.stem}.layout.html"

    else:
        raise SystemExit(
            f"layout export not supported for mode={mode}, provider={provider}"
        )

    results: dict[str, Path] = {}
    if want_html or want_pdf:
        render_html(layout, out_html)
        if want_html:
            results["layout_html"] = out_html
        if want_pdf:
            out_pdf = out_html.with_suffix(".pdf")
            render_pdf(out_html, out_pdf)
            results["layout_pdf"] = out_pdf
    return results


if __name__ == "__main__":
    main()
