FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git nodejs build-essential passwd && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./requirements.txt
# yq_common 通过内网 Nexus（仅 HTTP，https 握手实测失败）获取，wheel 已在
# requirements.txt 中用 sha256 内容哈希锁定。TLS 需等 Nexus 侧升级后才能真正启用；
# 在此之前不得删除 --trusted-host n.17track.net，否则 HTTP 源会因证书校验被拒绝。
# 不要改为 --require-hashes：那会要求全部传递依赖也提供哈希，当前仓库无法满足。
RUN python -m pip install --upgrade pip && python -m pip install --trusted-host n.17track.net -r requirements.txt
COPY core ./core
COPY infrastructure ./infrastructure
COPY agents ./agents
COPY workflow ./workflow
COPY server ./server
COPY config ./config
# workbench merged into server
COPY server.py ./server.py
COPY __init__.py ./__init__.py
# uid 10001 与 docker-compose 的 APP_UID 默认值一致；HOME 必须显式指向 app 用户的
# 主目录——compose 以数字 user 运行时容器不会自动设置 HOME，git 提交、httpx 等
# 依赖会退回 "/" 或无主目录而失败。
# /var/log/yqtrack 必须在镜像里建好并授权：yq_common 的日志初始化在 Linux 下
# 固定写 /var/log/yqtrack/<project>，它吞掉 makedirs 的 PermissionError 后，
# RotatingFileHandler 会因目录不存在在 import 期抛 FileNotFoundError，
# api 容器因此崩溃重启、frontend 的 depends_on: service_healthy 永不满足。
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app /app/runtime /var/log/yqtrack/py.ai.bug.fixer \
    && chown -R app:app /app /home/app /var/log/yqtrack
ENV HOME=/home/app
USER app
EXPOSE 8000
CMD ["python", "server.py"]
