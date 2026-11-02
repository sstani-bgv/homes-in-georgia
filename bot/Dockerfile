# One container: Telegram bot (foreground) + admin.py + refresh loop. See docs/README.md "Сервер (Dokploy)".
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PY=python LLM_PROVIDER=openai TG_FETCH=web ADMIN_HOST=0.0.0.0 ADMIN_PORT=8080

WORKDIR /app
# ffmpeg: yt-dlp (fetch_videos.py) turns ss.ge HLS videos into mp4 and merges YouTube 720p video+audio; ffprobe reads w/h
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-embed.txt ./
RUN pip install -r requirements.txt
# ===== EMBEDDINGS: opt-in, see requirements-embed.txt =====
ARG WITH_EMBED=0
RUN if [ "$WITH_EMBED" = 1 ]; then \
      pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1 \
      && pip install -r requirements-embed.txt; fi

# ===== CODEX CLI: opt-in (LLM_PROVIDER=codex, ChatGPT subscription); login lives in CODEX_HOME on the data volume =====
ARG WITH_CODEX=0
ARG CODEX_VERSION=0.160.1
RUN if [ "$WITH_CODEX" = 1 ]; then \
      python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/codex.tgz')" \
        "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz" \
      && tar -xzf /tmp/codex.tgz -C /tmp && mv /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
      && chmod +x /usr/local/bin/codex && rm -f /tmp/codex.tgz && codex --version; fi
ENV CODEX_HOME=/app/data/codex

RUN useradd --create-home --uid 1000 app
# includes docs/user-journey/index.html: the mockups page served at /journey (see .dockerignore)
COPY --chown=app:app . .
# data/ (SQLite, listings.json, client photos) and raw/ (fetched posts, photos, index/geo caches) are volumes;
# listings.json lives in data/ so the built base survives redeploys.
RUN mkdir -p data raw/img seed && ln -sf data/listings.json listings.json \
    && chmod +x start.sh refresh.sh && chown -R app:app /app
USER app
VOLUME ["/app/data", "/app/raw"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"ADMIN_PORT\",\"8080\")}/login', timeout=4)"

CMD ["./start.sh"]
