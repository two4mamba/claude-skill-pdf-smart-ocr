"""Convert the OCR-produced .md to additional formats (.docx, .pdf).

Used by extract.py after the OCR step finishes. Standalone CLI:

    python convert.py --md input.md --formats docx,pdf
    python convert.py --md input.md --formats docx --out other_dir

Strategy:
- .md  : no-op (the source).
- .docx: pandoc subprocess (high fidelity for headings, lists, tables, images).
- .pdf : pandoc → docx → pdf via Word (docx2pdf, Windows) OR LibreOffice
         headless (`soffice --convert-to pdf`, macOS / Linux). Platform
         dispatch happens automatically in `docx_to_pdf()`.

Layout preservation NOTE:
  This script only preserves logical structure (headings, lists, tables, image
  placement order). It does NOT reproduce the original PDF's pixel-level layout.
  For true layout preservation, see the layout JSON saved alongside (e.g.
  <stem>.layout.json from baidu provider, or MinerU's _middle.json) and the
  Phase-2 plan in 扫描图像PDF_OCR技术研究.md §13.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


# Match a centering <div ...><img src="..." .../></div> wrapper as a unit, OR
# a bare <img src="..." />. Used to normalize PaddleOCR-VL output (which emits
# HTML <img> wrapped in centering <div> blocks) into pandoc-friendly markdown
# image syntax. Without this, pandoc passes the raw HTML through to docx as
# text — no embedded image.
_DIV_IMG_RE = re.compile(
    r'<div\b[^>]*>\s*<img\b[^>]*\bsrc=(["\'])([^"\']+)\1[^>]*/?>\s*</div>',
    flags=re.IGNORECASE,
)
_BARE_IMG_RE = re.compile(
    r'<img\b[^>]*\bsrc=(["\'])([^"\']+)\1[^>]*/?>',
    flags=re.IGNORECASE,
)


def normalize_html_img_to_md(text: str) -> str:
    """Strip <div>...<img.../></div> wrappers and convert HTML img tags to
    markdown image syntax so pandoc embeds the actual image bytes."""
    text = _DIV_IMG_RE.sub(lambda m: f"\n\n![]({m.group(2)})\n\n", text)
    text = _BARE_IMG_RE.sub(lambda m: f"![]({m.group(2)})", text)
    return text


def log(msg: str) -> None:
    print(f"[pdf-smart-ocr/convert] {msg}", flush=True)


def have_pandoc() -> bool:
    return shutil.which("pandoc") is not None


def have_docx2pdf() -> bool:
    try:
        import docx2pdf  # noqa: F401
        return True
    except ImportError:
        return False


def find_libreoffice() -> str | None:
    """Locate the LibreOffice CLI on macOS/Linux. Returns full path or None.

    Checks PATH for `soffice` / `libreoffice`, plus the default macOS app
    bundle which doesn't ship a symlink into PATH by default.
    """
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "darwin":
        mac_default = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if mac_default.exists():
            return str(mac_default)
    return None


def md_to_docx(md_path: Path, docx_path: Path) -> Path:
    """md → docx via pandoc. Best fidelity for structure + tables."""
    if not have_pandoc():
        raise SystemExit(
            "pandoc not found. Install: winget install JohnMacFarlane.Pandoc"
        )
    # Preprocess: rewrite <img src="..."> HTML tags as ![](...) markdown so
    # pandoc actually embeds the images instead of passing through raw HTML.
    raw = md_path.read_text(encoding="utf-8")
    normalized = normalize_html_img_to_md(raw)
    if normalized != raw:
        # Write a sibling temp file so pandoc's --resource-path still works.
        tmp_md = md_path.with_suffix(".__pandoc_in.md")
        tmp_md.write_text(normalized, encoding="utf-8")
        input_md = tmp_md
    else:
        input_md = md_path
        tmp_md = None

    cmd = [
        "pandoc",
        str(input_md),
        "-f", "gfm+tex_math_dollars",
        "-t", "docx",
        "-o", str(docx_path),
        # Resolve relative image paths from the .md's directory so embedded
        # figures appear in the docx.
        "--resource-path", str(md_path.parent),
    ]
    log(f"pandoc → {docx_path.name}")
    try:
        subprocess.check_call(cmd)
    finally:
        if tmp_md and tmp_md.exists():
            tmp_md.unlink()
    return docx_path


def _docx_to_pdf_word(docx_path: Path, pdf_path: Path) -> Path:
    """Windows path: docx2pdf drives Microsoft Word via COM."""
    if not have_docx2pdf():
        raise SystemExit(
            "docx2pdf not installed. Install with: pip install docx2pdf "
            "(requires Microsoft Word installed on Windows)."
        )
    from docx2pdf import convert as _docx2pdf_convert
    log(f"docx2pdf (Word COM) → {pdf_path.name}")
    _docx2pdf_convert(str(docx_path), str(pdf_path))
    return pdf_path


def _docx_to_pdf_libreoffice(docx_path: Path, pdf_path: Path) -> Path:
    """macOS / Linux path: LibreOffice headless conversion.

    `soffice --convert-to pdf` writes <docx_stem>.pdf into --outdir, ignoring
    any custom output filename. We point --outdir at pdf_path.parent and
    rename afterwards if the caller asked for a non-default basename.
    """
    soffice = find_libreoffice()
    if soffice is None:
        raise SystemExit(
            "LibreOffice not found. Install one of:\n"
            "  macOS:  brew install --cask libreoffice\n"
            "  Ubuntu: sudo apt install libreoffice\n"
            "  Fedora: sudo dnf install libreoffice\n"
            "Or, if you have Microsoft Word installed, "
            "`pip install docx2pdf` and run on Windows / macOS-with-Word."
        )
    out_dir = pdf_path.parent
    cmd = [
        soffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(out_dir),
        str(docx_path),
    ]
    log(f"libreoffice → {pdf_path.name}")
    subprocess.check_call(cmd)
    produced = out_dir / f"{docx_path.stem}.pdf"
    if produced != pdf_path:
        if pdf_path.exists():
            pdf_path.unlink()
        produced.rename(pdf_path)
    return pdf_path


def docx_to_pdf(docx_path: Path, pdf_path: Path) -> Path:
    """docx → pdf. Routes by platform:

    - Windows  → docx2pdf (Microsoft Word via COM)
    - macOS / Linux → LibreOffice headless (`soffice --convert-to pdf`)

    docx2pdf does not work on Linux (no Word) and depends on AppleScript on
    macOS, so we prefer LibreOffice off Windows. macOS users with Word can
    still install docx2pdf and call _docx_to_pdf_word() directly.
    """
    if sys.platform == "win32":
        return _docx_to_pdf_word(docx_path, pdf_path)
    return _docx_to_pdf_libreoffice(docx_path, pdf_path)


def md_to_pdf(md_path: Path, pdf_path: Path, tmp_dir: Path | None = None) -> Path:
    """md → pdf. Best path: md → docx → pdf via docx2pdf (Word COM).

    Fallback: raise an actionable error. Pure-Python fallbacks (reportlab from
    scratch, xhtml2pdf) produce poor quality for CJK + structured content, so
    we prefer to fail clearly than silently degrade.
    """
    tmp_dir = tmp_dir or md_path.parent
    intermediate_docx = tmp_dir / f"{md_path.stem}.__convert_tmp.docx"
    try:
        md_to_docx(md_path, intermediate_docx)
        docx_to_pdf(intermediate_docx, pdf_path)
    finally:
        if intermediate_docx.exists():
            intermediate_docx.unlink()
    return pdf_path


def export(md_path: Path, formats: Iterable[str], out_dir: Path | None = None) -> dict[str, Path]:
    """Export the markdown to the requested formats.

    Returns a dict {format: output_path}.
    """
    out_dir = out_dir or md_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {"md": md_path}
    stem = md_path.stem

    for fmt in formats:
        fmt = fmt.lower().strip().lstrip(".")
        if fmt == "md":
            continue  # source
        elif fmt == "docx":
            results["docx"] = md_to_docx(md_path, out_dir / f"{stem}.docx")
        elif fmt == "pdf":
            results["pdf"] = md_to_pdf(md_path, out_dir / f"{stem}.pdf", tmp_dir=out_dir)
        else:
            raise SystemExit(f"Unsupported format: {fmt!r}. Use md / docx / pdf.")

    return results


def main():
    ap = argparse.ArgumentParser(description="Convert OCR markdown to docx/pdf.")
    ap.add_argument("--md", required=True, help="Path to input .md")
    ap.add_argument("--formats", default="docx,pdf",
                    help="Comma-separated list (md/docx/pdf). Default: docx,pdf")
    ap.add_argument("--out", default=None,
                    help="Output directory (default: same as input .md)")
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    if not md_path.exists():
        raise SystemExit(f"Markdown not found: {md_path}")
    out_dir = Path(args.out).resolve() if args.out else None
    formats = [f for f in args.formats.split(",") if f]
    results = export(md_path, formats, out_dir)
    for fmt, p in results.items():
        log(f"  {fmt}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
