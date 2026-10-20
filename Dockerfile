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
RUN useradd --create-home --uid 10001 app && mkdir -p /app/runtime && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["python", "server.py"]
