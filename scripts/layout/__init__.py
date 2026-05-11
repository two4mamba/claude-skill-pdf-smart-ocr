"""Layout-preserving export pipeline.

Path C from the research doc (§12.2): each OCR engine that emits bbox data
(baidu PaddleOCR-VL, MinerU) is normalized into NormalizedLayout, then rendered
to HTML with absolute positioning, then optionally to PDF via Playwright/Chromium.

Public surface:
    from layout import NormalizedLayout, NormalizedPage, NormalizedBlock
    from layout import adapt_baidu, adapt_mineru
    from layout import render_html, render_pdf
"""
from .normalized import NormalizedBlock, NormalizedLayout, NormalizedPage

__all__ = [
    "NormalizedBlock",
    "NormalizedLayout",
    "NormalizedPage",
]
