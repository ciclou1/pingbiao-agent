"""向量化客户端：批量调用 embed 服务。

设计原则：**向量是增强项，不是依赖**。服务未启用/不可用时返回 None，
调用方自动降级为关键词检索，评标流程不会因为向量服务挂了而停摆。
"""
import logging

import httpx

from .config import settings

log = logging.getLogger("embed")

BATCH = 16


def is_enabled() -> bool:
    return bool(settings.embed_service_url)


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量向量化。返回 None 表示不可用（调用方降级）。"""
    if not is_enabled() or not texts:
        return None
    url = settings.embed_service_url.rstrip("/") + "/embed"
    out: list[list[float]] = []
    try:
        with httpx.Client(timeout=600.0) as client:
            for i in range(0, len(texts), BATCH):
                resp = client.post(url, json={"texts": texts[i : i + BATCH]})
                resp.raise_for_status()
                out.extend(resp.json().get("vectors") or [])
        if len(out) != len(texts):
            log.warning("向量数量不匹配（%s/%s），本次降级", len(out), len(texts))
            return None
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("向量化失败，本次降级为关键词检索：%s", str(exc)[:200])
        return None


def to_pgvector(vec: list[float]) -> str:
    """转成 pgvector 的文本输入格式 '[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
