FROM python:3.11-slim

# 国内构建默认走清华镜像；海外构建可通过 --build-arg 覆盖。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# 先装依赖，利用 Docker 构建缓存；版本固定，避免镜像构建时被新版本破坏。
COPY requirements-docker.txt ./
RUN python -m pip install --no-cache-dir --timeout 60 --retries 5 \
    --index-url "$PIP_INDEX_URL" -r requirements-docker.txt

# 只复制运行所需的 Python 代码和已经构建好的前端产物。
COPY arena ./arena
COPY web/dist ./web/dist
COPY web_server.py ./

# 使用非 root 用户；数据目录由镜像初始化，便于命名卷直接写入。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

# 必须保持单进程：房间和会话都保存在进程内存中，不能用多个 worker。
CMD ["python", "web_server.py", "--host", "0.0.0.0", "--port", "8000"]
