"""MinerU `_middle.json` → NormalizedLayout.

MinerU 3.1.6 pipeline backend produces:
    {
      "pdf_info": [
        {
          "page_idx": 0,                           # 0-indexed!
          "page_size": [W, H],                     # in PDF points (72 dpi)
          "preproc_blocks": [...],                 # main content blocks
          "discarded_blocks": [...],               # page numbers, decorations
          "para_blocks": [...]                      # post-processed (we ignore)
        },
        ...
      ],
      "_backend": "pipeline",
      "_version_name": "..."
    }

Block shape (from BlockType enum):
    {
      "type": "title|text|image|table|chart|list|...",
      "bbox": [x1, y1, x2, y2],                    # corner format
      "index": 1,                                  # reading order
      "level": 2,                                  # title heading level (titles only)
      "lines": [                                   # for text-based blocks
        {
          "bbox": [...],
          "spans": [
            {"type": "text|image|...",
             "bbox": [...],
             "content": "...",                     # text spans
             "image_path": "<sha>.jpg"}            # image spans
          ]
        }
      ],
      "blocks": [...],                             # for visual blocks (image/table/chart):
                                                   # nested .blocks[0].lines[0].spans[0]
                                                   # is where image_path lives
    }

Coordinate conventions:
    - bbox values are in **PDF points** (72 dpi) — NOT pixels
    - We pass through these values and set NormalizedPage.dpi = 72 so renderers
      can compute the right page size in inches/px.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..normalized import NormalizedBlock, NormalizedLayout, NormalizedPage


# MinerU block.type → NormalizedBlock.type
_TYPE_MAP = {
    # Headings / text
    "title": "paragraph_title",
    "doc_title": "paragraph_title",
    "paragraph_title": "paragraph_title",
    "text": "text",
    "list": "list",
    "index": "text",
    "abstract": "text",
    "ref_text": "text",
    "vertical_text": "text",
    "aside_text": "text",
    # Visual
    "image": "image",
    "image_body": "image",
    "header_image": "image",
    "footer_image": "image",
    "chart": "chart",
    "chart_body": "chart",
    "table": "table",
    "table_body": "table",
    # Math
    "interline_equation": "formula",
    "equation": "formula",
    # Captions / footnotes
    "caption": "caption",
    "image_caption": "caption",
    "table_caption": "caption",
    "chart_caption": "caption",
    "image_footnote": "caption",
    "table_footnote": "caption",
    "chart_footnote": "caption",
    "footnote": "caption",
    "page_footnote": "caption",
    # Headers / footers
    "header": "header",
    "footer": "footer",
    "page_number": "footer",
    # Special
    "seal": "seal",
    "code": "code",
    "code_body": "code",
    "algorithm": "code",
}


def _bbox_corners_to_xywh(bbox) -> tuple[float, float, float, float]:
    """[x1, y1, x2, y2] → (x, y, w, h)."""
    if not bbox or len(bbox) != 4:
        return (0.0, 0.0, 0.0, 0.0)
    x1, y1, x2, y2 = bbox
    return (float(x1), float(y1), float(x2 - x1), float(y2 - y1))


def _extract_text_content(block: dict) -> str:
    """Concatenate all text spans across all lines into a single string."""
    parts: list[str] = []
    for line in block.get("lines") or []:
        line_parts: list[str] = []
        for span in line.get("spans") or []:
            sp_type = span.get("type", "")
            if sp_type == "text":
                line_parts.append(span.get("content", "") or "")
            elif sp_type in ("inline_equation",):
                eq = span.get("content", "") or ""
                line_parts.append(f"${eq}$" if eq else "")
        if line_parts:
            parts.append("".join(line_parts))
    return "\n".join(parts)


def _extract_image_path(block: dict, asset_dirname: str) -> Optional[str]:
    """For image/chart/table blocks, dig into nested blocks → lines → spans
    to find the image_path span. Return path relative to layout.html
    (i.e. '<asset_dirname>/<sha>.jpg').
    """
    # MinerU image blocks have nested 'blocks': [{'type': 'image_body', 'lines': ...}]
    candidates = block.get("blocks") or [block]
    for sub in candidates:
        for line in sub.get("lines") or []:
            for span in line.get("spans") or []:
                ip = span.get("image_path")
                if ip:
                    return f"{asset_dirname}/{ip}"
    return None


def _extract_table_html(block: dict) -> str:
    """Tables: MinerU stores HTML <table>...</table> as a span content."""
    candidates = block.get("blocks") or [block]
    for sub in candidates:
        for line in sub.get("lines") or []:
            for span in line.get("spans") or []:
                # html_content / content / table_body span
                html = span.get("html") or span.get("content") or ""
                if html and "<table" in html.lower():
                    return html
    return ""


def _adapt_block(
    raw_block: dict,
    asset_dirname: str,
    fallback_order: int,
) -> NormalizedBlock:
    raw_type = raw_block.get("type", "text")
    mapped = _TYPE_MAP.get(raw_type, "other")

    bbox = _bbox_corners_to_xywh(raw_block.get("bbox"))
    raw_index = raw_block.get("index")
    order = int(raw_index) if raw_index is not None else fallback_order

    image_path: Optional[str] = None
    content: str = ""

    if mapped in ("image", "chart"):
        image_path = _extract_image_path(raw_block, asset_dirname)
        # Captions if any baked into the same block
        content = _extract_text_content(raw_block)
    elif mapped == "table":
        content = _extract_table_html(raw_block) or _extract_text_content(raw_block)
    else:
        content = _extract_text_content(raw_block)

    return NormalizedBlock(
        type=mapped,
        bbox=bbox,
        content=content,
        reading_order=order,
        image_path=image_path,
    )


def adapt_mineru(
    middle_json_path: Path,
    asset_dir: str,
    source_pdf: str,
    asset_url_prefix: Optional[str] = None,
) -> NormalizedLayout:
    """Convert MinerU middle.json → NormalizedLayout.

    Parameters
    ----------
    middle_json_path : Path
        Path to <stem>_middle.json (in the auto/ subdir of MinerU output).
    asset_dir : str
        Filesystem path to the directory containing extracted images. MinerU
        stores them at <output>/<stem>/auto/images/.
    source_pdf : str
        Original PDF basename for display/debug.
    asset_url_prefix : str, optional
        URL-style prefix written into HTML img src, relative to the final
        layout.html location. Defaults to ``asset_dir``'s basename.
    """
    middle_json_path = Path(middle_json_path)
    raw = json.loads(middle_json_path.read_text(encoding="utf-8"))

    asset_root = Path(asset_dir)
    url_prefix = asset_url_prefix if asset_url_prefix is not None else asset_root.name

    pages: list[NormalizedPage] = []
    for page_idx, page_obj in enumerate(raw.get("pdf_info", [])):
        page_num = int(page_obj.get("page_idx", page_idx)) + 1  # convert 0→1 indexed
        size = page_obj.get("page_size") or [0, 0]
        width = float(size[0]) if len(size) >= 1 else 0.0
        height = float(size[1]) if len(size) >= 2 else 0.0

        blocks: list[NormalizedBlock] = []

        # Main content blocks
        for i, b in enumerate(page_obj.get("preproc_blocks") or []):
            blocks.append(_adapt_block(b, url_prefix, fallback_order=i))

        # Discarded blocks (page numbers, decorations) — keep them so the
        # rendered page LOOKS like the original. Mark with high reading_order
        # so they sort to the end.
        for j, b in enumerate(page_obj.get("discarded_blocks") or []):
            nb = _adapt_block(b, url_prefix, fallback_order=10000 + j)
            blocks.append(nb)

        # Stable sort by reading_order
        blocks.sort(key=lambda b: b.reading_order)

        pages.append(NormalizedPage(
            page_num=page_num,
            width_px=width,
            height_px=height,
            blocks=blocks,
            dpi=72,  # MinerU emits PDF-point coordinates (72 dpi)
        ))

    return NormalizedLayout(
        source_pdf=source_pdf,
        pages=pages,
        asset_dir=url_prefix,
        source_engine="mineru",
    )
