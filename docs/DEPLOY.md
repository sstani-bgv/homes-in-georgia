# Выкатка: заметки к переходам, которые нельзя сделать простым `git pull && up -d`

Все команды на сервере из каталога проекта:

```bash
C="docker compose -f deploy/docker-compose.yml --env-file .env"
```

## База: postgres:16-alpine → pgvector/pgvector:pg16 (этап 1, миграция 0006)

Миграция 0006 требует расширение `vector`, его нет в `postgres:16-alpine`. Поэтому образ базы сменился на
`pgvector/pgvector:pg16`, а сама база переезжает **через dump/restore на новый том** `pgvector`.

**Почему нельзя просто сменить образ на старом томе.** Alpine собран на musl, pgvector-образ на Debian/glibc. Локаль
та же (`en_US.utf8`), но строки сортируются по-разному: musl `1 A B _c a b ä`, glibc `1 a A ä b B _c`. Все btree-индексы
по тексту (`listings.id`, логины, телефоны...) построены в старом порядке и на glibc становятся битыми **без
предупреждения** (на musl Postgres не пишет версию коллации, сверять нечего). Проверено локально: старый том в новом
образе стартует молча, а `bt_index_check('listings_pkey')` падает с `item order invariant violated`. Итог: пропущенные
строки в поиске по индексу и дубли в уникальных ключах. Dump/restore строит индексы заново и этой проблемы не имеет.
Ещё мелочь: у postgres в alpine uid 70, в Debian 999 (entrypoint сам делает chown, но это ещё одна причина не мешать тома).

Порядок (простой бота и CRM на несколько минут):

```bash
$C stop api worker bot refresh                          # пишущих в базу больше нет; db ещё старый
$C exec -T db psql -U homes -d homes -tAc "select count(*) from listings"   # запомнить
$C exec -T db pg_dump -U homes -Fc homes > /root/homes-before-pgvector.dump
ls -lh /root/homes-before-pgvector.dump
git pull                                                # compose с новым образом и томом pgvector
$C up -d db                                             # новый образ, пустой том, база homes создаётся сама
$C exec -T db pg_restore -U homes -d homes --no-owner < /root/homes-before-pgvector.dump
$C exec -T db psql -U homes -d homes -tAc "select count(*) from listings"   # то же число
$C up -d --build                                        # api: alembic upgrade head → 0006 (vector + колонки)
```

(`homes` = `POSTGRES_USER` / `POSTGRES_DB` из `.env`, если они там другие, подставить свои.)

Откат: вернуть прошлый коммит `deploy/docker-compose.yml` и `$C up -d`. Старый том `pg` не тронут, но в нём данные на
момент дампа; всё, что записано после, осталось только в томе `pgvector`. Том `pg` удалить (`docker volume rm`), когда
новая база проработает пару дней.

Если забыть про restore и сразу сделать `up -d --build`, поднимется пустая база (api создаст схему с нуля). Данные при
этом не теряются: они в томе `pg` и в дампе, восстановить можно теми же командами (остановить api/worker/bot/refresh,
`pg_restore --clean`).

## Фото и видео в S3 (этап 1)

1. Создать бакет (Cloudflare R2 или Hetzner Object Storage) и ключ доступа только к нему. Бакет приватный: CRM отдаёт
   временные (presigned) ссылки. `S3_PUBLIC_URL` ставить, только если файлы должны быть публичными (сайт, Instagram).
2. В `.env` прописать `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`; `MEDIA_STORAGE` пока `local`.
3. `$C up -d --force-recreate api worker bot` и скопировать файлы:
   `$C exec api sh -c "cd backend && python -m app.storage copy --listing-photos"`.
   Повторный запуск безопасен: файл, который уже лежит в бакете с тем же размером, пропускается.
4. `MEDIA_STORAGE=s3` в `.env`, снова `$C up -d --force-recreate api worker bot`.
5. Ещё раз `copy`: доливает то, что агенты успели загрузить между шагами 3 и 4.
6. Проверить в CRM карточку объекта с фото и публикацию в канал. Локальные файлы (том `media`) удаляются вручную
   и только после проверки; `copy` их никогда не удаляет.

Фото объявлений бота (`bot/raw/img`, поле `listings.photos`) бот пока читает с диска; `--listing-photos` только кладёт
их копию в бакет под ключами `bot/raw/img/<файл>` на будущее.

## Эмбеддинги

`python -m app.embeddings backfill` (в каталоге backend) заполняет `properties.text_embedding` / `photo_embedding`.
Нужны torch и transformers (`bot/requirements-embed.txt`). В образ `deploy/Dockerfile` они пока не входят, поэтому
там команда ничего не делает; ставить их будем вместе с поиском похожих (этап 4).

## Один бот и userbot оператора (этап 9)

Отдельного рабочего бота больше нет: `STAFF_BOT_TOKEN` удалён, клиентам и сотрудникам пишет один бот (`BOT_TOKEN`).
Если `STAFF_BOT_TOKEN` остался в `.env`, его можно стереть; если панель открывали из рабочего бота, Mini App теперь
открывается только из основного (`/panel`), а основного бота нужно добавить в группу операторов, если его там нет.

Бот шлёт клиенту только анкету и карточки объектов (❤️ / 👎 / Ещё). Всё остальное приходит клиенту в личку
оператора: userbot (аккаунт оператора) отправляет inline-результат бота `<вид>:<token>` с кнопками, нажатия приходят
боту. Виды: `slots` (время просмотра, после выбора на карточке «⏰ Опаздываю» / «🔁 Перенести»), `remind`
(напоминание за час), `survey` (как прошёл просмотр), `reason` (причина отказа), `t14` (три оценки через 14 дней).
Свободный текст клиента (своё время, комментарий к отказу, проблема после T14) — это сообщение в личку оператору,
userbot сохраняет его в заявку. Нужно:

1. **Inline-режим бота.** В @BotFather: `/setinline` → выбрать бота → любой placeholder (например «slots»). Без
   этого inline-запрос не проходит, и userbot шлёт карточку текстом с нумерованными вариантами (клиент отвечает
   цифрой, T14 — тремя цифрами «3 2 3»).
2. **Сессия оператора.** `OPERATOR_USERBOT_SESSION` — Telethon StringSession аккаунта оператора, `API_ID`/`API_HASH`
   с https://my.telegram.org того же аккаунта. Сессию создаёт сам оператор на своём компьютере (команда в начале
   `backend/app/telegram/userbot.py`), вводит код из Telegram сам; строка = полный вход в аккаунт, кладётся только в
   `.env` сервера, при утечке — завершить сеанс в Telegram → Настройки → Устройства. Для первого живого теста
   оператором будет рабочий аккаунт владельца.
3. **Сервис.** В `.env`: `USERBOT_URL=http://userbot:8091`, `BOT_USERNAME` = username бота (без @),
   `OPERATOR_TG_USERNAME` = username аккаунта оператора (на него ведёт кнопка после ❤️). В
   `deploy/docker-compose.yml` раскомментировать сервис `userbot`, затем `$C up -d userbot bot api worker`.
   При старте userbot записывает свой Telegram id в `settings.userbot_tg_id`: бот отвечает на эти inline-запросы
   только этому аккаунту.

Миграция `0013` (таблица `inline_cards`: токены карточек) применяется как обычно при старте api. Без userbot
«Отправить клиенту» возвращает «Userbot оператора не подключён», заявка остаётся у агента без отправленного времени;
напоминание, опрос, причина отказа и T14 не уходят, а в истории заявки появляется «Не отправлено клиенту: userbot
оператора».
