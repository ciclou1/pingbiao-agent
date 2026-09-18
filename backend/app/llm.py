"""LLM 客户端：OpenAI 兼容网关 + 重试 + 模型降级 + 用量留痕（PRD 5.4 统一规范）。"""
import asyncio
import json
import logging
import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import LlmCall

log = logging.getLogger("llm")

RETRY_DELAYS = (1, 3, 6)
# 单次调用的**总时长**上限：httpx 的 timeout 是单个读写操作的超时，
# 上游慢速滴流时每次 read 都不超时、整通调用可能无限拖住任务 → 必须用 wait_for 兜总时长
TOTAL_TIMEOUT = 180.0


class LlmError(RuntimeError):
    pass


def _loads_json(text: str):
    """解析模型返回的 JSON；容忍 markdown 代码块与**输出被截断**两种情况。

    截断很常见（推理模型思考占了 completion 额度）：JSON 尾部缺失时，
    取最后一个完整对象补上闭合括号救回部分结果，远比整轮重试（实测 75 秒）划算。
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").lstrip()
        if t.startswith("json"):
            t = t[4:]
    t = t.strip()
    try:
        return json.loads(t)
    except (json.JSONDecodeError, TypeError):
        pass
    # 截断修复：从后往前找最后一个完整的对象边界，补 ] } 或 } 再试
    cut = t.rfind("}")
    while cut > 0:
        head = t[: cut + 1]
        for suffix in ("]}", "}]}", "}", "]}"):
            try:
                salvaged = json.loads(head + suffix)
                log.warning("LLM 输出被截断，已救回部分结果（%s 字符）", len(head))
                return salvaged
            except (json.JSONDecodeError, TypeError):
                continue
        cut = t.rfind("}", 0, cut)
    return None


async def _log_call(purpose: str, model: str, usage: dict, seconds: float, ok: bool, err: str) -> None:
    """用量写独立短会话，避免与业务事务互相干扰。"""
    from .database import SessionLocal

    try:
        async with SessionLocal() as s:
            s.add(
                LlmCall(
                    purpose=purpose,
                    model=model,
                    prompt_tokens=(usage or {}).get("prompt_tokens"),
                    completion_tokens=(usage or {}).get("completion_tokens"),
                    duration_ms=int(seconds * 1000),
                    ok=ok,
                    error=(err or "")[:1000],
                )
            )
            await s.commit()
    except Exception:
        log.exception("llm 用量写入失败")


async def chat_json(
    db: AsyncSession,
    *,
    purpose: str,
    system: str,
    user: str,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> dict:
    """调用网关 chat/completions（JSON 模式），返回解析后的 dict。

    重试策略：每模型 3 次（1s/3s/6s 退避）→ 降级到 fallback 模型；
    网关对 response_format 报 400 时自动去掉该参数重试（靠 prompt + 宽容解析兜底）。
    每次调用写 llm_calls 留痕。
    """
    if not settings.llm_base_url or not settings.llm_api_key:
        raise LlmError("LLM 网关未配置（LLM_BASE_URL / LLM_API_KEY）")
    models = [m for m in (settings.llm_model, settings.llm_model_fallback) if m]
    if not models:
        raise LlmError("未配置 LLM_MODEL")

    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    last_err = ""

    for model in models:
        for attempt in range(3):
            started = time.monotonic()
            usage: dict = {}
            err = ""
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                }
                if max_tokens:
                    payload["max_tokens"] = max_tokens
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await asyncio.wait_for(
                        client.post(url, json=payload, headers=headers), timeout=TOTAL_TIMEOUT
                    )
                    if resp.status_code == 400:
                        # 部分模型不支持 response_format：去掉重发一次
                        payload.pop("response_format")
                        resp = await asyncio.wait_for(
                            client.post(url, json=payload, headers=headers), timeout=TOTAL_TIMEOUT
                        )
                if resp.status_code != 200:
                    err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                else:
                    data = resp.json()
                    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                    usage = data.get("usage") or {}
                    result = _loads_json(content)
                    if result is None:
                        err = f"返回非 JSON: {content[:200]}"
                    else:
                        await _log_call(purpose, model, usage, time.monotonic() - started, True, "")
                        return result
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                err = f"{type(e).__name__}: {str(e)[:300]}"
            await _log_call(purpose, model, usage, time.monotonic() - started, False, err)
            last_err = err
            log.warning("llm %s attempt %d model %s failed: %s", purpose, attempt + 1, model, err)
            await asyncio.sleep(RETRY_DELAYS[attempt])

    raise LlmError(f"LLM 调用失败（已重试与降级）：{last_err}")
