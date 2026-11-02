# Build from the repo root:  docker build -f backend/Dockerfile -t homes-api .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend:/app TZ=Asia/Tbilisi
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
# The SPA is served from /app/frontend/dist when present: the deploy build adds it (multi-stage node build or a
# volume), e.g.  COPY --from=frontend /src/dist frontend/dist
RUN mkdir -p /app/data/media
WORKDIR /app/backend
EXPOSE 8000
# api (default); worker: `python -m app.worker`; migrations: `alembic upgrade head`
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
