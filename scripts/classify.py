"""Classify a PDF: text-PDF vs image-PDF, and recommend an extraction route.

Usage:
    python classify.py <pdf_path>

Prints a single JSON line. Exit 0 on success, non-zero on failure.
"""
import json
import sys
from pathlib import Path


SMALL_PAGE_THRESHOLD = 50      # ≤ this → Claude vision
VLM_PAGE_THRESHOLD = 100       # ≤ this (and > SMALL) → cloud VLM API
TEXT_DENSITY_THRESHOLD = 50    # avg chars per page below this → image PDF


def classify(pdf_path: str) -> dict:
    import pdfplumber

    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(pdf_path)

    with pdfplumber.open(p) as pdf:
        n_pages = len(pdf.pages)
        sample_n = min(n_pages, 10)
        # sample evenly across the doc
        if n_pages <= sample_n:
            indices = list(range(n_pages))
        else:
            step = n_pages // sample_n
            indices = [i * step for i in range(sample_n)]

        text_chars = 0
        for i in indices:
            try:
                t = pdf.pages[i].extract_text() or ""
            except Exception:
                t = ""
            text_chars += len(t.strip())

    avg = text_chars / max(1, len(indices))
    has_text_layer = avg >= TEXT_DENSITY_THRESHOLD

    if has_text_layer:
        rec = "text"
    elif n_pages <= SMALL_PAGE_THRESHOLD:
        rec = "image_small"
    elif n_pages <= VLM_PAGE_THRESHOLD:
        rec = "image_vlm"
    else:
        rec = "image_large"

    return {
        "pages": n_pages,
        "sampled_pages": len(indices),
        "text_chars": text_chars,
        "avg_chars_per_page": round(avg, 1),
        "has_text_layer": has_text_layer,
        "recommendation": rec,
    }


def main():
    if len(sys.argv) != 2:
        print("Usage: classify.py <pdf_path>", file=sys.stderr)
        sys.exit(2)
    result = classify(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
