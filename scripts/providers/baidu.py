"""Baidu official PaddleOCR-VL provider via AI Studio (aistudio.baidu.com/paddleocr).

This is the **AI Studio Spark Community** path, NOT the enterprise cloud
(aip.baidubce.com) path. Users get a free tier (first 1000 pages free) by
visiting https://aistudio.baidu.com/paddleocr — the page hands them BOTH:

  - an API_URL (a personal hosted endpoint already ending in /layout-parsing,
    e.g. https://xxx.aistudio-app.com/layout-parsing)
  - a TOKEN (an opaque string passed in the Authorization header)

Set both as User env vars:

    [Environment]::SetEnvironmentVariable('BAIDU_API_URL',     '<url>',   'User')
    [Environment]::SetEnvironmentVariable('BAIDU_ACCESS_TOKEN','<token>', 'User')

Then this provider POSTs the entire PDF to API_URL and gets back markdown
plus image URLs synchronously. We download the images and save them locally.

Reference: https://ai.baidu.com/ai-doc/AISTUDIO/Dmh4onssk
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .base import VLMProvider, OcrError


class BaiduProvider(VLMProvider):
    name = "baidu"
    default_model = "paddleocr-vl"  # nominal; the URL itself selects the model
    env_var = "BAIDU_ACCESS_TOKEN"  # primary env var for error messages
    supports_pdf = True

    REQUEST_TIMEOUT_S = 30 * 60  # large PDFs can take a while
    IMAGE_DOWNLOAD_TIMEOUT_S = 120

    # ------------------------------------------------------------------
    # Endpoint + auth
    # ------------------------------------------------------------------
    def _endpoint(self) -> str:
        url = os.environ.get("BAIDU_API_URL")
        if not url:
            raise OcrError(
                "Missing BAIDU_API_URL. Get it from https://aistudio.baidu.com/paddleocr "
                "(click the 'API' button on the right; the page shows API_URL + TOKEN). "
                "Then: [Environment]::SetEnvironmentVariable('BAIDU_API_URL','<url>','User')"
            )
        url = url.strip().rstrip("/")
        # The page-supplied URL usually already ends in /layout-parsing. Don't
        # double-append, but tolerate users who paste the bare host.
        if not url.endswith("/layout-parsing"):
            url = url + "/layout-parsing"
        return url

    def _token(self) -> str:
        token = os.environ.get("BAIDU_ACCESS_TOKEN")
        if not token:
            raise OcrError(
                "Missing BAIDU_ACCESS_TOKEN. Get it from https://aistudio.baidu.com/paddleocr "
                "(same page as the API_URL). "
                "Then: [Environment]::SetEnvironmentVariable('BAIDU_ACCESS_TOKEN','<token>','User')"
            )
        return token

    # ------------------------------------------------------------------
    # ocr_image: required by ABC. Wrap a single image as a one-page PDF
    # so we can hit the same endpoint.
    # ------------------------------------------------------------------
    def ocr_image(self, image_bytes, *, model=None, lang="ch"):
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PDF")
            pdf_bytes = buf.getvalue()
        except ImportError:
            raise OcrError(
                "Baidu image-mode needs Pillow. pip install pillow. "
                "Or call ocr_pdf() with a real PDF directly."
            )
        md, images, _ = self.ocr_pdf(pdf_bytes, "page.pdf", model=model, lang=lang)
        return md, images

    # ------------------------------------------------------------------
    # ocr_pdf: preferred entry point. Synchronous request.
    # ------------------------------------------------------------------
    def ocr_pdf(self, pdf_bytes, file_name, *, model=None, lang="ch"):
        endpoint = self._endpoint()
        token = self._token()

        # Detect PDF vs image (file_name is hint; magic bytes are authoritative)
        is_pdf = pdf_bytes[:4] == b"%PDF" or file_name.lower().endswith(".pdf")
        file_type = 0 if is_pdf else 1

        # Match the official sample's payload shape exactly. Keep optional
        # toggles minimal (only ones we've verified the endpoint supports).
        payload = {
            "file": base64.b64encode(pdf_bytes).decode("ascii"),
            "fileType": file_type,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={
                "Authorization": f"token {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT_S) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise OcrError(
                f"Baidu /layout-parsing failed ({e.code}) at {endpoint}: {err_body[:600]}"
            ) from e
        except urllib.error.URLError as e:
            raise OcrError(f"Network error to {endpoint}: {e.reason}") from e

        if resp.get("errorCode", 0) != 0:
            raise OcrError(
                f"Baidu API error {resp.get('errorCode')}: "
                f"{resp.get('errorMsg', 'unknown')}"
            )

        result = resp.get("result") or {}
        pages = result.get("layoutParsingResults") or []
        if not pages:
            raise OcrError(
                f"Baidu response had no layoutParsingResults: {json.dumps(resp)[:500]}"
            )

        md_parts: list[str] = []
        images: dict[str, bytes] = {}
        layout_json = {"pages": []}

        for i, page in enumerate(pages, 1):
            md_obj = page.get("markdown") or {}
            page_md = md_obj.get("text", "") or ""
            page_imgs = md_obj.get("images") or {}

            # `images` is {relative_path: image_url}. Per the official sample,
            # we GET each URL to fetch raw bytes (NOT base64 in the response).
            for rel_path, img_url in page_imgs.items():
                if not img_url:
                    continue
                try:
                    raw = self._download_bytes(img_url)
                except Exception as e:
                    print(f"[baidu] skip image p{i} {rel_path}: {e}", flush=True)
                    continue
                # Flatten with per-page prefix
                base_name = rel_path.replace("\\", "/").rsplit("/", 1)[-1] or "img.jpg"
                fname = f"page-{i:03d}-{base_name}"
                images[fname] = raw
                # PaddleOCR-VL emits image refs in BOTH forms:
                #   - HTML:     <img src="imgs/foo.jpg" ... />
                #   - Markdown: ![alt](imgs/foo.jpg)
                # Rewrite the path text directly so both forms get patched.
                # Bare filename is intentional: extract.py prepends the assets
                # dirname downstream (one place to control the prefix).
                page_md = page_md.replace(rel_path, fname)

            md_parts.append(page_md.strip())
            layout_json["pages"].append({
                "page_num": i,
                "is_start": md_obj.get("isStart"),
                "is_end": md_obj.get("isEnd"),
                "pruned_result": page.get("prunedResult"),
            })

        md = "\n\n".join(p for p in md_parts if p)
        return md, images, layout_json

    @staticmethod
    def _download_bytes(url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=BaiduProvider.IMAGE_DOWNLOAD_TIMEOUT_S) as r:
            return r.read()
