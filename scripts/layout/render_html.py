"""NormalizedLayout → HTML with absolute-positioned blocks.

Each page becomes a <div class="page" style="width:Wpx; height:Hpx;">, and
each NormalizedBlock becomes an absolutely-positioned <div> inside that page
at its bbox coordinates. CSS lives in templates/page.css.

DPI handling: baidu emits px @ ~150dpi; MinerU emits pt @ 72dpi. We render at
the source's native unit by setting the page <div> to bbox units 1:1, then use
the layout's `dpi` field to know whether 1 unit = 1/150 in or 1/72 in. The
browser doesn't care about the absolute physical size in screen mode, but
Playwright's page.pdf() does — render_pdf passes the page size in inches.

We intentionally use plain string formatting rather than Jinja2 to avoid
adding a dependency. The output is small and predictable.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from .normalized import NormalizedBlock, NormalizedLayout, NormalizedPage


import math


def _infer_font_size_px(
    bbox_w: float,
    bbox_h: float,
    content: str,
) -> Optional[float]:
    """Infer a font size **in CSS pixels** that lets `content` fit inside the bbox.

    We output px (not pt) on purpose: CSS converts pt → px by 4/3 internally,
    which inflates fonts beyond the bbox when source bbox values are in pt
    (e.g. MinerU at 72dpi). Using px directly keeps font size on the same
    numeric scale as the bbox, which is exactly what we want — the bbox is
    treated as raw CSS px regardless of which OCR engine produced it.

    Single-line blocks:  font_px = bbox_h / line_height
    Multi-line blocks:   font_px = sqrt(area / chars / line_height)

    Take min of the two as a safety bound, with a small headroom factor so
    text rarely brushes the bbox edge.
    """
    if bbox_h <= 0 or bbox_w <= 0:
        return None

    # PDF text bboxes are typically laid out at line-height ~1.0–1.25 (each
    # line's bbox IS the line). 1.4 (web default) inflates beyond what fits.
    LINE_HEIGHT = 1.2
    HEADROOM = 0.90  # slight margin for descenders / kerning

    def _em_width(s: str) -> float:
        """Approximate em-width of a string. CJK ≈ 1, ASCII ≈ 0.55."""
        w = 0.0
        for c in s:
            if c.isspace():
                w += 0.4
            elif ord(c) < 128:
                w += 0.55
            else:
                w += 1.0
        return w

    raw = content or ""
    visible_total = "".join(c for c in raw if not c.isspace())
    if not visible_total:
        return None

    # Hard-break-aware path: when the adapter joins source lines with '\n'
    # (MinerU's per-span structure), CSS white-space:pre-wrap honors them.
    # We must size the font so that (1) no source line wraps within bbox_w,
    # AND (2) all source lines stack vertically inside bbox_h.
    segments = [seg for seg in raw.split("\n") if seg.strip()]
    if len(segments) >= 2:
        max_seg_em = max(_em_width(s) for s in segments) or 1.0
        n_segments = len(segments)
        # Horizontal fit: longest segment must fit in one visual line
        f_h = bbox_w / max_seg_em
        # Vertical fit: n_segments stacked
        f_v = bbox_h / (n_segments * LINE_HEIGHT)
        est = min(f_h, f_v) * HEADROOM
    else:
        # No hard breaks — use area-based estimate with ceil safety factor
        single_px = bbox_h / LINE_HEIGHT
        area_px2 = bbox_w * bbox_h
        char_em_squared = _em_width(visible_total)
        if char_em_squared > 0:
            multi_px = math.sqrt(area_px2 / (char_em_squared * LINE_HEIGHT))
        else:
            multi_px = single_px
        est = min(single_px, multi_px) * HEADROOM

    # Sanity range — assume CSS-px font-size between 6 and 36 is plausible.
    if est < 6:
        return 6.0
    if est > 36:
        return None  # implausibly large; let CSS default apply
    return round(est, 1)


def _escape_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _escape_text(value: str) -> str:
    return html.escape(value, quote=False)


class _TableHTMLSanitizer(HTMLParser):
    """Allow-list sanitizer for table block HTML.

    Chromium will execute scripts injected into the layout HTML when generating
    layout_pdf. block.content for table blocks originates from untrusted PDF
    content via MinerU and must not be inserted verbatim.
    """

    _ALLOWED_TAGS = frozenset({
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "col",
        "colgroup",
    })
    _ALIGN_VALUES = frozenset({"left", "center", "right", "justify"})
    _ATTRS_BY_TAG = {
        "td": frozenset({"colspan", "rowspan", "align"}),
        "th": frozenset({"colspan", "rowspan", "align"}),
        "tr": frozenset({"align"}),
        "col": frozenset({"span"}),
        "colgroup": frozenset({"span"}),
    }
    _NUMERIC_ATTRS = frozenset({"colspan", "rowspan", "span"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag not in self._ALLOWED_TAGS:
            return

        safe_attrs = self._safe_attrs(tag, attrs)
        attr_text = "".join(
            f' {name}="{_escape_attr(value)}"' for name, value in safe_attrs
        )
        self._parts.append(f"<{tag}{attr_text}>")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ALLOWED_TAGS:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(_escape_text(data))

    def handle_entityref(self, name: str) -> None:
        self._parts.append(_escape_text(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._parts.append(_escape_text(f"&#{name};"))

    def get_html(self) -> str:
        return "".join(self._parts)

    @classmethod
    def _safe_attrs(
        cls,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> list[tuple[str, str]]:
        allowed = cls._ATTRS_BY_TAG.get(tag, frozenset())
        safe: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed or value is None:
                continue
            value = value.strip()
            if name == "align":
                value = value.lower()
                if value not in cls._ALIGN_VALUES:
                    continue
            elif name in cls._NUMERIC_ATTRS:
                if not value.isdigit() or int(value) < 1:
                    continue
            safe.append((name, value))
        return safe


def _sanitize_table_html(value: str) -> str:
    sanitizer = _TableHTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.get_html()


def _block_inner_html(block: NormalizedBlock) -> str:
    """Render the inside of a block <div>. Type-specific."""
    if block.type in ("image", "chart"):
        if block.image_path:
            return (
                f'<img src="{_escape_attr(block.image_path)}" '
                f'alt="{_escape_attr(block.type)}" />'
            )
        return f'<span class="missing-image">[{block.type} (image missing)]</span>'

    if block.type == "table":
        c = (block.content or "").strip()
        sanitized = _sanitize_table_html(c)
        return sanitized if sanitized.strip() else _escape_text(c)

    if block.type == "formula":
        # LaTeX content; render as raw text (browser users can use MathJax later).
        return _escape_text(block.content or "")

    # Default: text content with line breaks preserved by white-space:pre-wrap
    return _escape_text(block.content or "")


def _block_html(block: NormalizedBlock, dpi: int) -> str:
    x, y, w, h = block.bbox
    font_px = _infer_font_size_px(w, h, block.content or "")
    style_parts = [
        f"left:{x:.1f}px",
        f"top:{y:.1f}px",
        f"width:{w:.1f}px",
        f"height:{h:.1f}px",
    ]
    if font_px is not None:
        style_parts.append(f"font-size:{font_px:.1f}px")
    style = ";".join(style_parts)
    cls = f"block block-{block.type}"
    inner = _block_inner_html(block)
    return f'  <div class="{cls}" style="{style}">{inner}</div>'


def _page_html(page: NormalizedPage, source_engine: str) -> str:
    page_style = f"width:{page.width_px:.1f}px;height:{page.height_px:.1f}px"
    blocks_html = "\n".join(_block_html(b, page.dpi) for b in page.blocks)
    meta = f'<div class="page-meta">p.{page.page_num} · {source_engine} · {int(page.width_px)}×{int(page.height_px)}{"px" if page.dpi >= 100 else "pt"}</div>'
    # data-dpi lets render_pdf convert page dimensions to inches using the
    # source coordinate system (baidu ≈ 150 dpi px, MinerU = 72 dpi pt)
    # instead of assuming the CSS-default 96 dpi.
    return (
        f'<div class="page" style="{page_style}" '
        f'data-page="{page.page_num}" data-dpi="{page.dpi}">\n'
        f'{meta}\n'
        f'{blocks_html}\n'
        f'</div>'
    )


def _full_html(layout: NormalizedLayout, css: str) -> str:
    title = _escape_text(layout.source_pdf or "Layout")
    pages_html = "\n\n".join(_page_html(p, layout.source_engine) for p in layout.pages)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
{pages_html}
</body>
</html>
"""


def render_html(layout: NormalizedLayout, out_html: Path) -> Path:
    """Render NormalizedLayout to a single HTML file.

    Reads the bundled page.css. The HTML references images via the
    NormalizedLayout.asset_dir (relative path), so the assets dir must be a
    sibling of out_html on disk for the HTML to display correctly.
    """
    out_html = Path(out_html)
    css_path = Path(__file__).parent / "templates" / "page.css"
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(_full_html(layout, css), encoding="utf-8")
    return out_html
