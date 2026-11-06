# Homes in Georgia Platform

Единая система агентства. Заменяет Google Sheets, Apps Script, CRM, мини-аппы и бот-прототип.

- **backend/**: FastAPI + PostgreSQL. Здесь обе воронки, сделки и комиссии, отчёты, T0/T14, сроки (SLA) и напоминания (worker), импорт из Google Sheets, публикация объектов в TG-канал.
- **frontend/**: CRM на React в дизайн-системе агентства и мини-апп для агентов в Telegram (`/m/*`).
- **bot/**: бот-подборщик, работает на общей базе. Анкета клиента, ❤️ → заявка, группа операторов, слоты времени, контакты, итог просмотра, T14.
- **deploy/**: `docker-compose.yml` (db, api, worker, bot, refresh). Все ключи берутся только из `.env`.
- **docs/**: [ARCHITECTURE.md](docs/ARCHITECTURE.md) (схема, API, Telegram-контракт), [SECURITY.md](docs/SECURITY.md) (что утекло в старых репо), [SOURCES.md](docs/SOURCES.md) (Livo, Korter, Facebook, Instagram), [user-journey.txt](docs/user-journey.txt).

## Запуск на сервере

```bash
cp .env.example .env    # заполнить BOT_TOKEN, SECRET_KEY, POSTGRES_PASSWORD, OPERATORS_CHAT_ID, PUBLIC_URL...
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
docker compose -f deploy/docker-compose.yml exec api sh -c "cd backend && python -m app.importers.sheets_csv /path/to/sheets-csv"
```

### Бот и нейронка

Бот подключается только через `.env`, код менять не нужно:

| Переменная | Что это |
|---|---|
| `BOT_TOKEN`, `BOT_USERNAME` | токен и username бота из @BotFather |
| `OPERATORS_CHAT_ID` | ID группы операторов, сюда приходят заявки |
| `SUPERADMIN_TG_IDS` | Telegram ID суперадминов через запятую (применяется, пока в базе нет ни одного суперадмина) |
| `OPENAI_API_KEY` | ключ для поиска квартир; без него анкета работает, а подборка нет |
| `OPENAI_BASE_URL`, `LLM_MODEL` | для совместимого API (например ProxyAPI) и выбора модели, по умолчанию `gpt-4.1-mini` |

### Узнавание объектов (этап 4)

Каждое новое объявление при `bot/db.py sync` сверяется с базой объектов (`backend/app/services/matching.py`): тот же
объект привязывается сам, похожий ждёт решения оператора в «Новых объявлениях». Один раз после выката:
`cd backend && python -m app.services.matching hashes` (хэши фото старых объявлений), затем `... matching run`
(сверить объявления, пришедшие до этапа 4).

### Фото и видео (S3)

По умолчанию файлы CRM лежат на диске (`MEDIA_DIR`). Чтобы хранить их в S3-совместимом хранилище (Cloudflare R2, Hetzner, MinIO):

| Переменная | Что это |
|---|---|
| `MEDIA_STORAGE` | `local` (по умолчанию) или `s3` |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION` | адрес хранилища, бакет, регион (`us-east-1` подходит для R2 и MinIO) |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | ключи доступа к бакету |
| `S3_PUBLIC_URL` | необязательно: публичный адрес бакета, тогда ссылки прямые, а не временные (presigned) |

Перенос уже загруженных файлов (повторный запуск ничего не дублирует): `cd backend && python -m app.storage copy`,
с фото объявлений бота (`bot/raw/img`): `... copy --listing-photos`. Порядок переключения и переход базы на pgvector: [docs/DEPLOY.md](docs/DEPLOY.md).

В @BotFather: `/setprivacy` → Disable (иначе бот не видит сообщения в группе), `/setdomain` → домен из `PUBLIC_URL`
(вход в CRM через Telegram). Бота добавить в группу операторов админом.

## Локальная разработка (без Docker)

```bash
scripts/dev-pg.sh                                   # PostgreSQL 16 в ~/.hig-pg (unix-socket), pgvector уже внутри
cd backend && ../.venv/bin/alembic upgrade head && ../.venv/bin/python -m app.seed_demo   # в .env: APP_ENV=dev, DEV_CORS=true
../.venv/bin/uvicorn app.main:app --reload --port 8000     # CRM: http://localhost:8000 (вход nino / demo)
../.venv/bin/python -m app.worker                   # сроки и напоминания
cd .. && .venv/bin/python -m bot.run                # бот (нужен BOT_TOKEN)
cd frontend && npm run dev                          # фронт с hot reload, проксирует /api на :8000
```

## Тесты

```bash
cd backend && ../.venv/bin/python -m pytest -q                      # 99 тестов (S3-тест: TEST_S3_* → локальный MinIO)
cd bot && ../.venv/bin/python -m unittest discover -s test          # 104 теста, e2e через fake Bot API
cd frontend && npm run build                                        # проверка типов + сборка
```

S3-тест против локального MinIO (без этих переменных он пропускается):

```bash
docker run -d --name hig-minio -p 9000:9000 -e MINIO_ROOT_USER=minio -e MINIO_ROOT_PASSWORD=minio12345 bitnamilegacy/minio
export TEST_S3_ENDPOINT=http://localhost:9000 TEST_S3_BUCKET=hig-test TEST_S3_ACCESS_KEY=minio TEST_S3_SECRET_KEY=minio12345
```
