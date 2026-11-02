# Homes in Georgia Platform

Единая система агентства. Заменяет Google Sheets, Apps Script, CRM, мини-аппы и бот-прототип.

- **backend/**: FastAPI + PostgreSQL. Здесь обе воронки, сделки и комиссии, отчёты, T0/T14, сроки (SLA) и напоминания (worker), импорт из Google Sheets, прокси для генератора постов.
- **frontend/**: CRM на React в дизайн-системе агентства и мини-апп для агентов в Telegram (`/m/*`).
- **bot/**: бот-подборщик, работает на общей базе. Анкета клиента, ❤️ → заявка, группа операторов, слоты времени, контакты, итог просмотра, T14.
- **deploy/**: `docker-compose.yml` (db, api, worker, bot, refresh). Все ключи берутся только из `.env`.
- **docs/**: [ARCHITECTURE.md](docs/ARCHITECTURE.md) (схема, API, Telegram-контракт), [SECURITY.md](docs/SECURITY.md) (что утекло в старых репо), [user-journey.txt](docs/user-journey.txt).

## Запуск на сервере

```bash
cp .env.example .env    # заполнить BOT_TOKEN, SECRET_KEY, POSTGRES_PASSWORD, OPERATORS_CHAT_ID, PUBLIC_URL...
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
docker compose -f deploy/docker-compose.yml exec api sh -c "cd backend && python -m app.importers.sheets_csv /path/to/sheets-csv"
```

## Локальная разработка (без Docker)

```bash
scripts/dev-pg.sh                                   # PostgreSQL в ~/.hig-pg (unix-socket)
cd backend && ../.venv/bin/alembic upgrade head && ../.venv/bin/python -m app.seed_demo   # в .env: APP_ENV=dev, DEV_CORS=true
../.venv/bin/uvicorn app.main:app --reload --port 8000     # CRM: http://localhost:8000 (вход nino / demo)
../.venv/bin/python -m app.worker                   # сроки и напоминания
cd .. && .venv/bin/python -m bot.run                # бот (нужен BOT_TOKEN)
cd frontend && npm run dev                          # фронт с hot reload, проксирует /api на :8000
```

## Тесты

```bash
cd backend && ../.venv/bin/python -m pytest -q                      # 57 тестов
cd bot && ../.venv/bin/python -m unittest discover -s test          # 91 тест, e2e через fake Bot API
cd frontend && npm run build                                        # проверка типов + сборка
```
