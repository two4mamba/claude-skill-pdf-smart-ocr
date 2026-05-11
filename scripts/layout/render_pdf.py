"""HTML → PDF via Playwright/Chromium.

Auto-installs the Playwright Python package and the Chromium browser binary
on first use (decision 4b in the Phase 2 plan). Uses sync_playwright to keep
the call surface trivial — no asyncio in extract.py.

Page size handling: each .page <div> in the HTML has its own width/height
declared inline. We let Chromium auto-detect from the first page (using
preferCSSPageSize-style behavior) by setting page.pdf(width=..., height=...)
to match what the HTML declares. Since a multi-page document with varying
page sizes can't be expressed in a single page.pdf() call, we accept the
common case (uniform page sizes) and pick the FIRST page's dimensions.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _ensure_playwright_installed() -> None:
    """Install playwright + Chromium if not present (decision 4b)."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        print(
            "[layout/render_pdf] playwright not installed — installing "
            "(this is one-time, ~5 MB)…",
            flush=True,
        )
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "playwright"],
        )

    # Verify Chromium binary is present by trying a minimal launch.
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
    except Exception:
        # Most likely "Executable doesn't exist" — install Chromium.
        print(
            "[layout/render_pdf] Chromium browser not installed — running "
            "`playwright install chromium` (one-time, ~150 MB download)…",
            flush=True,
        )
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
        )


# Match the first .page div's inline style + data-dpi attribute. The two
# attributes may appear in either order, so we look up data-dpi separately.
_PAGE_SIZE_RE = re.compile(
    r'class="page"[^>]*?style="[^"]*?width:\s*([\d.]+)px[^"]*?height:\s*([\d.]+)px',
    flags=re.IGNORECASE,
)
_PAGE_DPI_RE = re.compile(
    r'class="page"[^>]*?data-dpi="(\d+)"',
    flags=re.IGNORECASE,
)


def _detect_page_size_px(html_text: str) -> Optional[tuple[float, float]]:
    """Pull the first .page div's width/height (in px) out of the HTML."""
    m = _PAGE_SIZE_RE.search(html_text)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def _detect_source_dpi(html_text: str) -> int:
    """Pull data-dpi off the first .page div. Defaults to 96 (CSS px) when
    absent (e.g. hand-authored HTML)."""
    m = _PAGE_DPI_RE.search(html_text)
    return int(m.group(1)) if m else 96


def render_pdf(html_path: Path, pdf_path: Path) -> Path:
    """Render an HTML file to PDF.

    The HTML must declare each page's dimensions as inline style on its .page
    <div> elements. We use the first page's size as the PDF page size — this
    gives correct results for documents with uniform page sizes (the common
    case for PPT exports, papers, books). Documents with mixed page sizes get
    rendered at the first page's size with the rest scaled to fit.
    """
    _ensure_playwright_installed()

    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    html_text = html_path.read_text(encoding="utf-8")
    size = _detect_page_size_px(html_text)
    source_dpi = _detect_source_dpi(html_text)
    if size is None:
        # Fall back to A4 portrait, no scaling
        page_w_in, page_h_in, scale = "8.27in", "11.69in", 1.0
    else:
        # Page-div width/height are in source-engine units (baidu ≈ 150 dpi
        # CSS px, MinerU = 72 dpi PDF points). Two conversions matter:
        #
        # (1) Paper size:  inches = source_units / source_dpi.
        # (2) Content fit: Chromium treats CSS px as 1/96 inch, so to make the
        #     .page div (W source-units = W CSS px) fill the paper (W/source_dpi
        #     inches = W*96/source_dpi CSS px), we scale by 96/source_dpi.
        #
        # Skipping (1) → wrong physical paper (Baidu ~30% too tall, MinerU
        # ~25% too short). Skipping (2) → content occupies only part of the
        # paper, or overflows. We need both.
        page_w_in = f"{size[0] / source_dpi:.4f}in"
        page_h_in = f"{size[1] / source_dpi:.4f}in"
        # Playwright clamps scale to [0.1, 2.0]
        scale = max(0.1, min(2.0, 96 / source_dpi))

    file_url = html_path.as_uri()  # file:///C:/...

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(file_url, wait_until="networkidle")
            page.pdf(
                path=str(pdf_path),
                width=page_w_in,
                height=page_h_in,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                print_background=True,
                prefer_css_page_size=False,  # honor our explicit width/height
                scale=scale,
            )
        finally:
            browser.close()

    return pdf_path
