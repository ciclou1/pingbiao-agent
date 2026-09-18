FROM python:3.12-slim

WORKDIR /app
ENV TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONUNBUFFERED=1

# 系统依赖：libgl1/libglib2.0-0 供 OpenCV，libgomp1 是 PaddlePaddle 的 OpenMP 运行时
# （缺 libgomp1 时 PaddleOCR 会静默退化到 RapidOCR 兜底）
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 构建期预下载模型并烘焙进镜像，运行期不再联网
COPY warmup.py .
RUN python warmup.py || echo "模型预热失败，运行期首次调用再下载"

COPY app.py .

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8800 --workers ${OCR_WORKERS:-2}"]