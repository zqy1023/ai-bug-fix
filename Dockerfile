FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git nodejs build-essential passwd && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && python -m pip install --trusted-host n.17track.net -r requirements.txt
COPY app ./app
COPY config ./config
COPY workbench ./workbench
COPY server.py ./server.py
COPY __init__.py ./__init__.py
# uid 10001 与 docker-compose 的 APP_UID 默认值一致；HOME 必须显式指向 app 用户的
# 主目录——compose 以数字 user 运行时容器不会自动设置 HOME，git 提交、httpx 等
# 依赖会退回 "/" 或无主目录而失败。
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app /app/runtime \
    && chown -R app:app /app /home/app
ENV HOME=/home/app
USER app
EXPOSE 8000
CMD ["python", "server.py"]
