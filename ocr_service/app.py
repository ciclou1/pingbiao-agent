"""独立 OCR 服务：PP-OCR 中文识别（PaddleOCR / RapidOCR 同模型，自动择一）。

为什么独立成容器：
- PaddlePaddle + 模型体积大（约 1~1.5GB），塞进 api/worker 会让主镜像臃肿、构建变慢
- OCR 是可选能力：不需要时直接不启动该服务，主流程把扫描件标为「需人工」
- 模型在构建期下载并烘焙进镜像，运行期不再联网

接口：
  GET  /health           → {"status":"ok","engine":"paddleocr|rapidocr"}
  POST /recognize        → body: 图片二进制（png/jpg）→ {"text": "...", "lines": n, "ms": 123}
"""
import io
import logging
import os
import subprocess
import sys
import time

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ocr")

app = FastAPI(title="评标 OCR 服务")
_engine: tuple[str, object] | None = None

# auto：先探测 PaddleOCR 是否可用，可用则用，否则用 RapidOCR（同一套 PP-OCR 模型，ONNX 运行时）
ENGINE_PREF = os.getenv("OCR_ENGINE", "auto").lower()

_PADDLE_PROBE = (
    "from paddleocr import PaddleOCR;"
    "PaddleOCR(use_angle_cls=False, lang='ch', show_log=False);"
    "print('ok')"
)


def _paddle_healthy() -> bool:
    """在**子进程**里探测 PaddleOCR：PaddlePaddle 的初始化失败是段错误（杀进程），
    Python 层 try/except 抓不住，只能靠子进程退出码判断。"""
    try:
        r = subprocess.run([sys.executable, "-c", _PADDLE_PROBE], capture_output=True, timeout=300)
        if r.returncode == 0:
            return True
        log.warning("PaddleOCR 探测失败（退出码 %s）：%s", r.returncode, r.stderr.decode("utf-8", "ignore")[-300:])
    except subprocess.TimeoutExpired:
        log.warning("PaddleOCR 探测超时")
    return False


def load_engine():
    global _engine
    if _engine is not None:
        return _engine

    use_paddle = ENGINE_PREF == "paddleocr" or (ENGINE_PREF == "auto" and _paddle_healthy())
    if use_paddle:
        from paddleocr import PaddleOCR

        t0 = time.time()
        _engine = ("paddleocr", PaddleOCR(use_angle_cls=False, lang="ch", show_log=False))
        log.info("PaddleOCR 就绪，耗时 %.1fs", time.time() - t0)
    else:
        from rapidocr_onnxruntime import RapidOCR

        _engine = ("rapidocr", RapidOCR())
        log.info("RapidOCR 就绪（PP-OCR 模型 + ONNX 运行时）")
    return _engine


def _to_array(blob: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(blob)).convert("RGB")
    return np.array(img)


def _run(kind: str, engine, arr) -> list[str]:
    if kind == "paddleocr":
        res = engine.ocr(arr, cls=False)
        lines = (res[0] if res else None) or []
        return [str(item[1][0]) for item in lines if item and item[1]]
    res, _ = engine(arr)
    return [str(item[1]) for item in (res or [])]


@app.get("/health")
def health():
    return {"status": "ok", "engine": _engine[0] if _engine else "未加载"}


@app.post("/recognize")
async def recognize(request: Request):
    blob = await request.body()
    if not blob:
        return JSONResponse(status_code=400, content={"detail": "空请求体"})
    t0 = time.time()
    try:
        kind, engine = load_engine()
        arr = _to_array(blob)
        lines = _run(kind, engine, arr)
    except Exception as exc:  # noqa: BLE001
        log.exception("识别失败")
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
    ms = int((time.time() - t0) * 1000)
    return {"text": "\n".join(lines), "lines": len(lines), "engine": kind, "ms": ms}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8800")))
