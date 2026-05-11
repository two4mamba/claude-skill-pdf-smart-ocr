"""NormalizedLayout — provider-agnostic intermediate format for layout-preserving export.

Both baidu (block-level bbox) and MinerU (line/span-level bbox) get adapted
into this shape. Renderers downstream consume NormalizedLayout only — they
don't know which OCR engine produced it.

Coordinate system convention:
    - All bbox values are in pixels at `dpi` per page (default 150).
    - Origin (0, 0) is top-left of the page.
    - bbox = (x, y, w, h) where (x, y) is the top-left corner of the block.

Adapters MUST normalize coordinates to this convention before constructing
NormalizedBlock instances.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Block types — keep a closed enum so renderers can rely on it.
# ---------------------------------------------------------------------------
BLOCK_TYPES = frozenset({
    "paragraph_title",   # heading-like text
    "text",              # body paragraph
    "image",             # figure
    "table",             # table (content held as markdown or HTML)
    "formula",           # math (LaTeX in content)
    "chart",             # chart figure (treat like image visually)
    "header",            # page header (top decoration / running head)
    "footer",            # page footer
    "seal",              # stamp / signature
    "caption",           # figure/table caption
    "list",              # bulleted/numbered list item
    "code",              # code block
    "other",             # fallback
})


@dataclass
class NormalizedBlock:
    """A single positioned content block on a page."""
    type: str                                          # one of BLOCK_TYPES
    bbox: tuple[float, float, float, float]            # (x, y, w, h) in px
    content: str                                       # text / HTML / LaTeX / md table
    reading_order: int                                 # 0-indexed within page
    image_path: Optional[str] = None                   # relative path if type == "image"
    font_size_pt: Optional[float] = None               # inferred from bbox.h if known

    def __post_init__(self):
        if self.type not in BLOCK_TYPES:
            # Don't crash — just normalize unknown types to 'other' so renderers
            # still draw them. Adapters should map exotic types upstream.
            self.type = "other"
        if len(self.bbox) != 4:
            raise ValueError(f"bbox must be 4-tuple, got {self.bbox!r}")


@dataclass
class NormalizedPage:
    """One page with its dimensions and ordered blocks."""
    page_num: int                  # 1-indexed
    width_px: float
    height_px: float
    blocks: list[NormalizedBlock] = field(default_factory=list)
    dpi: int = 150                 # rendering dpi assumed when measuring px


@dataclass
class NormalizedLayout:
    """Full document layout."""
    source_pdf: str                # original PDF basename, no path
    pages: list[NormalizedPage] = field(default_factory=list)
    asset_dir: str = ""            # relative dir (from layout.html) where images live
    source_engine: str = ""        # 'baidu' | 'mineru' | ... — for debugging only

    # ----- serialization helpers (handy for debugging + caching) -----
    def to_json(self) -> str:
        def _default(obj):
            if isinstance(obj, tuple):
                return list(obj)
            raise TypeError
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=_default)

    def save(self, path: Path) -> Path:
        path.write_text(self.to_json(), encoding="utf-8")
        return path
