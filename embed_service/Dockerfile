FROM python:3.12-slim

WORKDIR /app
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# torch 走 CPU 专用源：默认 PyPI 的 torch 会连带 nvidia-* 依赖，镜像涨到 3GB+，
# CPU 源只有约 200MB，且本机无需 GPU。
# 加长 pip 重试与超时：大包（torch ~200MB）在带宽紧张时容易超时失败
RUN pip install --no-cache-dir --retries 5 --timeout 180 torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 180 -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt \
    || pip install --no-cache-dir --retries 5 --timeout 180 -r requirements.txt

COPY app.py .

# 模型目录由 compose 挂载（models/bge-m3），不烘焙进镜像：重建镜像不重下 2.3GB
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8900 --workers ${EMBED_WORKERS:-1}"]
