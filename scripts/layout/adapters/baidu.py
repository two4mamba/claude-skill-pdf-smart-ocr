"""baidu PaddleOCR-VL layout.json → NormalizedLayout.

The layout.json saved by extract.py for the baidu provider has shape:
    {
      "pages": [
        {
          "page_num": 1,
          "pruned_result": {
            "width": 1191,
            "height": 1684,
            "parsing_res_list": [
              {
                "block_label": "paragraph_title|text|image|table|...",
                "block_content": "<text>" or "" (empty for images),
                "block_bbox": [x1, y1, x2, y2],   # corner format!
                "block_order": 1,                  # reading order
                ...
              },
              ...
            ]
          }
        },
        ...
      ]
    }

Key conventions:
  - bbox is corner format [x1, y1, x2, y2] — convert to (x, y, w, h)
  - image block_content is empty; the actual image file lives in the assets dir
    under name `page-NNN-img_in_<type>_box_X1_Y1_X2_Y2.<ext>` — we index by
    (page_num, bbox_tuple) to find the file
  - units are pixels at the API's render DPI (typically ~150 — Baidu doesn't
    document this, but the file we tested at 1191×1684 px ≈ A4 @ 150dpi)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from ..normalized import NormalizedBlock, NormalizedLayout, NormalizedPage


# baidu block_label → NormalizedBlock.type
_LABEL_MAP = {
    "paragraph_title": "paragraph_title",
    "text": "text",
    "image": "image",
    "header_image": "image",      # decorative image at top
    "footer_image": "image",      # decorative image at bottom
    "chart": "chart",
    "table": "table",
    "formula": "formula",
    "header": "header",
    "footer": "footer",
    "number": "footer",           # page-number → treat as footer
    "vision_footnote": "caption", # small text near a figure
    "seal": "seal",
}

# Match image filenames like:
#   page-002-img_in_image_box_104_673_449_1065.jpg
#   page-005-img_in_header_image_box_1048_639_1110_710.jpg
_IMG_NAME_RE = re.compile(
    r"^page-(\d+)-img_in_\w+?_box_(\d+)_(\d+)_(\d+)_(\d+)\.\w+$"
)


def _index_assets(asset_dir: Path) -> dict[tuple[int, int, int, int, int], str]:
    """Build {(page_num, x1, y1, x2, y2) → filename} for image lookup."""
    if not asset_dir.exists():
        return {}
    index: dict[tuple[int, int, int, int, int], str] = {}
    for f in asset_dir.iterdir():
        m = _IMG_NAME_RE.match(f.name)
        if m:
            key = tuple(int(x) for x in m.groups())  # (page, x1, y1, x2, y2)
            index[key] = f.name
    return index


def _pick_image_file(
    asset_index: dict[tuple[int, int, int, int, int], str],
    page_num: int,
    bbox_corners: tuple[int, int, int, int],
) -> Optional[str]:
    """Look up the saved image filename for this image block by (page, bbox).

    Tolerates a 1-pixel rounding mismatch on each coord — baidu sometimes
    rounds floats differently in different parts of its response.
    """
    x1, y1, x2, y2 = bbox_corners
    for dx1 in (0, -1, 1):
        for dy1 in (0, -1, 1):
            for dx2 in (0, -1, 1):
                for dy2 in (0, -1, 1):
                    key = (page_num, x1 + dx1, y1 + dy1, x2 + dx2, y2 + dy2)
                    if key in asset_index:
                        return asset_index[key]
    return None


def _bbox_corners_to_xywh(bbox: list[float]) -> tuple[float, float, float, float]:
    """[x1, y1, x2, y2] → (x, y, w, h). Tolerates already-(x,y,w,h) input."""
    if len(bbox) != 4:
        raise ValueError(f"bbox must be 4 numbers, got {bbox!r}")
    x1, y1, x2, y2 = bbox
    # Heuristic: if x2 > x1 and y2 > y1 we treat as corners. (Always true
    # for baidu output in practice.)
    return (float(x1), float(y1), float(x2 - x1), float(y2 - y1))


def adapt_baidu(
    layout_json_path: Path,
    asset_dir: str,
    source_pdf: str,
    asset_url_prefix: Optional[str] = None,
) -> NormalizedLayout:
    """Convert baidu layout.json → NormalizedLayout.

    Parameters
    ----------
    layout_json_path : Path
        Path to <stem>.layout.json saved by extract.py.
    asset_dir : str
        Filesystem path to the assets directory containing extracted images.
        Used for image lookup.
    source_pdf : str
        Original PDF basename (no path). Stored for downstream display.
    asset_url_prefix : str, optional
        URL-style prefix written into HTML img src, relative to the final
        layout.html location. Defaults to ``asset_dir``'s basename (i.e.
        assumes layout.html sits next to the assets dir).
    """
    layout_json_path = Path(layout_json_path)
    raw = json.loads(layout_json_path.read_text(encoding="utf-8"))

    asset_root = Path(asset_dir)
    asset_index = _index_assets(asset_root)
    url_prefix = asset_url_prefix if asset_url_prefix is not None else asset_root.name

    pages: list[NormalizedPage] = []
    for page_obj in raw.get("pages", []):
        page_num = int(page_obj.get("page_num", len(pages) + 1))
        pruned = page_obj.get("pruned_result") or {}
        width = float(pruned.get("width", 0) or 0)
        height = float(pruned.get("height", 0) or 0)

        blocks: list[NormalizedBlock] = []
        raw_blocks = pruned.get("parsing_res_list") or []
        for raw_block in raw_blocks:
            label = raw_block.get("block_label", "other")
            mapped_type = _LABEL_MAP.get(label, "other")

            corners = raw_block.get("block_bbox") or [0, 0, 0, 0]
            xywh = _bbox_corners_to_xywh(corners)

            content = raw_block.get("block_content") or ""
            # Drop OCR placeholder noise: blocks whose content is only
            # decorative box characters (e.g. baidu emits "☐ ☐ ☐ ☐ ☐" when
            # it sees marker bullets it can't read). These add visual noise
            # without conveying information.
            stripped = content.strip()
            if stripped and all(c in "☐□■◻◽▢▣▪▫·•・ " for c in stripped):
                continue
            # block_order may be missing or explicitly None for footers/decor
            raw_order = raw_block.get("block_order")
            order = int(raw_order) if raw_order is not None else 9999

            image_path: Optional[str] = None
            if mapped_type in ("image", "chart"):
                fname = _pick_image_file(
                    asset_index,
                    page_num,
                    tuple(int(round(c)) for c in corners),  # type: ignore[arg-type]
                )
                if fname:
                    image_path = f"{url_prefix}/{fname}"

            blocks.append(NormalizedBlock(
                type=mapped_type,
                bbox=xywh,
                content=content,
                reading_order=order,
                image_path=image_path,
            ))

        # Stable sort by reading_order; preserves original order for ties.
        blocks.sort(key=lambda b: b.reading_order)

        pages.append(NormalizedPage(
            page_num=page_num,
            width_px=width,
            height_px=height,
            blocks=blocks,
            dpi=150,  # baidu's de facto render DPI for the test sample
        ))

    return NormalizedLayout(
        source_pdf=source_pdf,
        pages=pages,
        asset_dir=url_prefix,
        source_engine="baidu",
    )
