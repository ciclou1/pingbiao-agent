"""BGE-M3 向量化服务（CPU 推理；模型目录由宿主机挂载，见 docker-compose 的 MODEL_DIR）。

只做一件事：文本批量 → 1024 维归一化向量。
模型加载失败不退出进程（/embed 返回 503），主流程据此降级为关键词检索，
不会因为向量服务挂了就整个评标流程停摆。
"""
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("embed")

MODEL_DIR = os.getenv("MODEL_DIR", "/models/bge-m3")
BATCH = int(os.getenv("EMBED_BATCH", "8"))
MAX_LEN = int(os.getenv("EMBED_MAX_LEN", "512"))

app = FastAPI(title="BGE-M3 向量化服务")
_model = None
_load_error = ""


def get_model():
    global _model, _load_error
    if _model is None and not _load_error:
        try:
            t0 = time.time()
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_DIR, device="cpu")
            _model.max_seq_length = MAX_LEN
            log.info("模型就绪，耗时 %.1fs", time.time() - t0)
        except Exception as exc:  # noqa: BLE001
            _load_error = f"{type(exc).__name__}: {exc}"
            log.exception("模型加载失败")
    return _model


@app.get("/health")
def health():
    state = "ready" if _model is not None else ("error" if _load_error else "lazy")
    return {"status": state, "model_dir": MODEL_DIR, "error": _load_error}


@app.post("/embed")
async def embed(request: Request):
    body = await request.json()
    texts = [str(t) for t in (body.get("texts") or []) if str(t).strip()]
    if not texts:
        return JSONResponse(status_code=400, content={"detail": "texts 为空"})
    model = get_model()
    if model is None:
        return JSONResponse(status_code=503, content={"detail": f"模型不可用：{_load_error}"})
    t0 = time.time()
    vecs = model.encode(texts, batch_size=BATCH, normalize_embeddings=True, show_progress_bar=False)
    return {
        "dim": int(len(vecs[0])),
        "count": len(texts),
        "ms": int((time.time() - t0) * 1000),
        "vectors": [v.tolist() for v in vecs],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8900")))
