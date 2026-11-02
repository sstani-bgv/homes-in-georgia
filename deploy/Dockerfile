# One image for api / worker / bot / refresh (different commands in docker-compose.yml).
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend:/app TZ=Asia/Tbilisi
WORKDIR /app
# ffmpeg: bot video fetchers (yt-dlp merges/HLS)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
COPY bot/requirements.txt bot/requirements.txt
RUN pip install -r backend/requirements.txt -r bot/requirements.txt
COPY backend/ backend/
COPY bot/ bot/
COPY --from=web /web/dist frontend/dist
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data/media /app/bot/data /app/bot/raw && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "cd backend && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
