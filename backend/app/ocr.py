"""OCR 集成点（默认关闭）。

架构：OCR 能力放在**独立服务**（ocr_service/，PP-OCR 中文模型），主镜像保持精简；
需要时 `docker compose --profile ocr up -d ocr` 启动并把 OCR_ENABLED 置 true。

调用点已在 worker.parse_document 中接好：仅对「无文本层」的文档触发，
电子版页面直接跳过，不浪费时间。
"""
import logging

import httpx

from .config import settings

log = logging.getLogger("ocr")

# 渲染 DPI 与页级并发；并发数应与 OCR 服务端 worker 数匹配
OCR_DPI = 200


def is_enabled() -> bool:
    return bool(settings.ocr_enabled and settings.ocr_service_url)


def recognize_page(png_bytes: bytes) -> str:
    """单页图像 → 文本（调用 OCR 服务）。"""
    if not is_enabled():
        raise RuntimeError("OCR 未启用（需设置 OCR_ENABLED=true 与 OCR_SERVICE_URL）")
    url = settings.ocr_service_url.rstrip("/") + "/recognize"
    resp = httpx.post(url, content=png_bytes, timeout=180.0)
    resp.raise_for_status()
    return str(resp.json().get("text") or "")


def fill_pages(pages: list, raw: bytes, filename: str) -> int:
    """对无文本层的页做 OCR，把文本回填到 pages。返回补回的总字符数。

    未启用时直接返回 0（no-op），保证调用点代码路径可跑。
    页级并发（OCR_CONCURRENCY，默认 2）：扫描件常上百页，串行太慢；
    并发数应与 OCR 服务端的 uvicorn worker 数匹配。
    """
    if not is_enabled():
        return 0

    name = (filename or "").lower()
    if not name.endswith(".pdf"):
        log.warning("暂只支持对 PDF 做 OCR，跳过 %s", filename)
        return 0

    import fitz

    from concurrent.futures import ThreadPoolExecutor

    targets: list[tuple[int, bytes]] = []
    try:
        with fitz.open(stream=raw, filetype="pdf") as pdf:
            for page in pdf:
                target = next((p for p in pages if p.page_no == page.number + 1), None)
                if target is None or len((target.text or "").strip()) >= 20:
                    continue
                pix = page.get_pixmap(dpi=OCR_DPI)
                targets.append((target.page_no, pix.tobytes("png")))
    except Exception:
        log.exception("扫描件渲染失败（不影响其余解析）")
        return 0

    if not targets:
        return 0
    log.info("待 OCR 页数 %s（并发 %s），文件 %s", len(targets), settings.ocr_concurrency, filename)

    def _one(item):
        page_no, blob = item
        try:
            return page_no, recognize_page(blob)
        except Exception:
            log.exception("第 %s 页 OCR 失败", page_no)
            return page_no, ""

    filled = 0
    from .parser import normalize_ocr_text

    with ThreadPoolExecutor(max_workers=max(1, settings.ocr_concurrency)) as pool:
        for page_no, text in pool.map(_one, targets):
            cleaned = normalize_ocr_text(text)
            if cleaned:
                target = next((p for p in pages if p.page_no == page_no), None)
                if target is not None:
                    target.text = cleaned
                    filled += len(cleaned)
    return filled
