# Выкатка: заметки к переходам, которые нельзя сделать простым `git pull && up -d`

Все команды на сервере из каталога проекта:

```bash
C="docker compose -f deploy/docker-compose.yml --env-file .env"
```

## Миграции склеены в одну (0001, 10.11.2026)

Перед первым продом цепочка 0001–0014 заменена одной миграцией `0001` (старые файлы в истории git). Схема та же
(сверено `pg_dump -s`). Новая база создаётся как обычно (`alembic upgrade head` при старте api). База, которая уже
прошла старую цепочку (стенд 37.27.206.154), переводится без изменений схемы: `$C exec api sh -c "cd backend && alembic
stamp 0001 --purge"`, затем `$C up -d --build`. Разделы ниже про 0006/0013 относятся к таким старым базам.

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

## Старая база Давида (этап 3): объекты, история и медиа из рабочих групп

Файлы уже лежат в бакете `homes-in-georgia-media` (Hetzner, fsn1, проект Давида) под ключами `legacy/<группа>/<сообщение>.<расш>`;
там же `legacy/done.jsonl` (файл → объект → sha256) и `legacy/media_plan.jsonl`. Реестр объектов (`registry.json`)
собирается локально: `python -m app.importers.legacy_objects build ...` (см. docstring), склейки по фото — `--photo-merges`.

1. Бэкап базы (как перед любой выкаткой).
2. CRM должна смотреть в этот бакет: `S3_*` в `.env` от бакета `homes-in-georgia-media`, `MEDIA_STORAGE=s3`.
   Если CRM уже пишет в другой бакет — сначала перенести его файлы сюда (`rclone copy`), потом переключить.
3. Положить `registry.json` и `done.jsonl` (это данные, не код) в том `media` контейнера api:
   `$C exec api mkdir -p /app/data/legacy && docker cp registry.json homes-platform-api-1:/app/data/legacy/` (так же done.jsonl),
   CSV свежей таблицы — в `/app/data/legacy-sheets/`.
4. Свежая таблица: `$C exec api sh -c "cd backend && python -m app.importers.sheets_csv /app/data/legacy-sheets"`.
5. Загрузка: `$C exec api sh -c "cd backend && python -m app.importers.legacy_objects load --out /app/data/legacy
   --media /app/data/legacy/done.jsonl"`. Объект, который уже есть в CRM (тот же пост канала или кадастр), не создаётся
   заново: к нему дописываются история, публикации и фото. Повторный запуск ничего не задваивает.
6. Превью (640 px WebP, в т.ч. из HEIC): `$C exec api sh -c "cd backend && python -m app.storage previews"` — несколько
   часов на ~92 тыс. фото; CRM работает и без них (отдаёт оригинал), но HEIC без превью браузеры не показывают.
7. Проверить: число объектов `source_type='legacy'` ≈ числу в `summary.json`, карточка живого объекта с фото и постами.

## Каталог бота = объекты CRM (10.11.2026)

Бот ищет только по опубликованным объектам CRM. У каждого такого объекта есть строка каталога в `listings` (`id = obj<id
объекта>`, `source = 'object'`); её собирает `app/services/catalog.py` из объекта: поля, пост генератора, фото и видео из
хранилища. Объявления с сайтов в поиск бота не попадают: клиенту их отправляет только «быстрая проверка» агента
(services/demand.py). Строки обновляет worker на каждом проходе (меняются только изменившиеся, объект ушёл из базы —
строка `gone`) и публикация объекта сразу. Вручную: `$C exec api sh -c "cd backend && python -m app.services.catalog"`.
Фото объектов бот берёт из хранилища и кладёт JPEG до 2560 px в `bot/raw/objects` (том `botraw`).

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
2. **Сессия оператора — по QR-коду из CRM (с 10.11.2026).** Управление → Настройки → «Аккаунт оператора» →
   «Подключить по QR-коду»: оператор сканирует код телефоном (Telegram → Настройки → Устройства → Подключить
   устройство), при облачном пароле вводит его там же (пароль не сохраняется). Сессия хранится в базе
   (`settings.userbot_session`, API настроек её не отдаёт), userbot начинает работать без перезапуска. «Отключить»
   завершает сеанс в Telegram. Нужны только `API_ID`/`API_HASH` и `USERBOT_URL`. Старый способ ниже работает, пока
   вход по QR не сделан.
   Старый способ: `OPERATOR_USERBOT_SESSION` — Telethon StringSession аккаунта оператора, `API_ID`/`API_HASH`
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

## Сайт hge.ge из CRM (14.11.2026)

Сайт читает объекты из CRM, а не из парсера 8 каналов: `GET /api/public/site/listings` (без логина, тот же origin
за Caddy), `/listings/{id|старый id}`, `/photos/{id}/{n}?p=1`, `/video/{id}`. Три источника, одна форма объекта:
аренда (посуточно, коммерция в аренду) — `properties` (id `o<id>`), продажа / новостройки / коммерция в продажу —
`sale_objects` (`s<id>`), авто — `cars` (`c<id>`). В выдаче только `published` с фото и только публичные поля (без
собственника, жильца, кадастра, чеклиста, приватных файлов). Продажу и авто агенты ведут в CRM: меню «Продажа» и
«Авто» (`/api/offers/{sale|car}`), «Опубликовать» / «Снять с сайта»; правка сразу чистит кэш выдачи.

**Миграция 0019** (`alembic upgrade head`, идёт с обычным деплоем): таблицы `sale_objects` и `cars`. Старых данных не
трогает, откат — `alembic downgrade 0018` (таблицы удаляются вместе с объектами).

**Разовый импорт** текущих объектов сайта — выполнен на проде 14.11.2026 (431 создано, 98 связано с объектами CRM, 3858 фото), скрипт удалён; при необходимости он есть в истории git (коммит 5cf93f9). Как запускался:

    $C exec api sh -c "cd backend && python oneoff_import_hge.py --dry-run"
    $C exec api sh -c "cd backend && python oneoff_import_hge.py"

Берёт `https://hge.ge/data/listings.json` (`--source` — другой URL или файл). Аренда / посуточно / коммерция в аренду
становятся Property (`published`, без собственника, `post_fields.service/site_id`) + публикация `tg`; объект, у
которого уже есть этот `post_url`, только получает service/site_id, свои данные не меняются. Продажа, новостройки,
коммерция в продажу идут в `sale_objects`, авто — в `cars`. Фото (до 9) скачиваются с hge.ge и кладутся в хранилище
(`properties/<id>/…` и `offers/<s|c><id>/…`). Объект с уже известным старым id пропускается. `--dry-run` ничего не
пишет и не качает; итог: создано / найдено / фото / пропущено по сервисам. **После импорта на проде файл
`backend/oneoff_import_hge.py` удалить** (в приложение он не входит). Не-аренда в клиентский каталог бота не попадает.

Проверка: `curl -s https://admin.hge.ge/api/public/site/listings | head -c 600` (counts по сервисам, items с `id`
вида `o123` / `s45` / `c6` и `legacy_id` вида `rent-551776`), фото:
`curl -sI https://admin.hge.ge/api/public/site/photos/s45/0?p=1`. Выдача кэшируется 60 с, курсы НБГ — 12 ч.

**Сайт в Caddy** (`/opt/caddy/Caddyfile`, контейнер `caddy` в сети хоста; после правки `docker restart caddy`). Пока
DNS hge.ge не переключён, сайт открыт на тестовом адресе; `hge.ge, www.hge.ge` уже в блоке — после смены A-записей у proservice.ge Caddy сам получит сертификат:

    hge.ge, www.hge.ge, hge.91-99-162-108.sslip.io {
    	encode zstd gzip
    	handle /api/public/* {
    		reverse_proxy 127.0.0.1:8000
    	}
    	handle {
    		reverse_proxy 127.0.0.1:3000
    	}
    }

**Аналитика сайта** (миграция 0020, таблица `site_events`). Сайт шлёт анонимные события (`web/src/lib/analytics.ts`)
на `POST /api/public/site/events`, отчёт «Аналитика» в CRM (только суперадмины) считается из этой таблицы
(`services/site_analytics.py`, та же формула, что в Apps Script `site-analytics.gs`). Ключ `SITE_ANALYTICS_KEY` и
Apps Script больше не нужны. История из Google-таблицы «HGE Analytics» не переносится: она остаётся на старой
странице hge.ge/stats/ до переключения DNS.

## Логи (16.11.2026): каждая ошибка CRM видна на сервере

Отдельного раздела в CRM нет — всё в файлах. Каждый процесс пишет JSON-строки (`backend/app/logs.py`) в stdout
(`docker logs`) и в `/app/data/logs/<service>.log` в volume `media` — файлы переживают пересоздание контейнеров.
На хосте: `L=$(docker volume inspect deploy_media -f '{{.Mountpoint}}')/logs`.

- `api.log` — одна строка на каждый запрос `/api/*` (кроме health): `rid` (он же заголовок `X-Request-Id`, он же «код …» в
  тосте 5xx-ошибки на скриншоте), `user_id`/`user`/`role`, `method`, `path`, `query` (без токенов), `status`, `ms`, `ip`,
  `ua`; у 4xx/5xx — `error` (code + message) и `body` (JSON, пароли/токены/ключи/подписи → `***`, телефоны → `+995***56`);
  у 500 — `traceback`. Ошибки браузера (`window.onerror`, unhandled rejection, падение страницы — вместо белого экрана
  «Что-то пошло не так», сбои API ≥ 500 и сети) приходят через `POST /api/client-log` строкой `logger: app.client` с
  `url`, `version` (коммит фронта), `message`, `stack`.
- `worker.log`, `bot.log`, `userbot.log` — логи процессов с traceback'ами; `refresh.log` — вывод `refresh.sh` построчно
  (строки с error/failed/traceback — уровень WARNING).
- Ротация: файл в полночь (Тбилиси) → `api.log.2026-11-16`, хранится 30 дней. `docker logs` — 5 × 20 МБ на контейнер.

```bash
L=$(docker volume inspect deploy_media -f '{{.Mountpoint}}')/logs
grep -h '"status": 5' $L/api.log*                                   # все 5xx
grep -h '"level": "ERROR"' $L/*.log* | tail -50                       # все ошибки всех сервисов (+ ошибки браузера)
grep -h '"logger": "app.client"' $L/api.log* | tail                   # ошибки во фронте
grep -h '"user_id": 12,' $L/api.log* | grep -v '"status": 200' | tail  # что не получилось у пользователя 12
grep -h '"user": "Нино"' $L/api.log* | tail                           # по имени
grep -h 'a1b2c3d4e5' $L/*.log*                                        # по коду из скриншота (X-Request-Id)
```

Выкатка с версией: `GIT_COMMIT=$(git rev-parse --short HEAD) docker compose ... up -d --build` — коммит попадает в
строку «api started, version …» и в `version` ошибок фронта (без него там время сборки).

## Авто: каналы машин ⇄ CRM ⇄ сайт (17.11.2026)

**Импорт из каналов.** Воркер каждые `CAR_IMPORT_MINUTES` (15) читает веб-версию публичных каналов
`t.me/s/CarsSaleInGeorgia` и `t.me/s/CarsRentInGeorgia` (`backend/app/services/car_import.py`; без сессии и без прав
бота) и ведёт «Авто» в CRM: новый пост → новая машина (`source=telegram`, фото копируются, сразу на сайте); правка
поста → машина перечитывается; `#Sold` / `#Rented` / удалённый пост → архив; повторный пост той же арендной машины
(модель, год, цена, период) — та же машина на новом посте. Посты, опубликованные из CRM, не дублируются.
Первый прогон вручную (сначала без записи):

    $C exec worker sh -c "cd backend && python -m app.services.car_import --dry-run"
    $C exec worker sh -c "cd backend && python -m app.services.car_import"

**Генератор постов** (`backend/app/services/car_post.py`) пишет точный формат каждого канала с хештегами
(марка, кузов, десятилетие, топливо, двигатель, пробег, цена). Форма «Авто» показывает пост вживую.
**Публикация** из CRM идёт в канал машины: `CAR_SALE_CHAT_ID` / `CAR_RENT_CHAT_ID`, по умолчанию
`@CarsSaleInGeorgia` / `@CarsRentInGeorgia`. **Бот должен быть администратором этих каналов** (право публикации).
Миграция **0029** (`alembic upgrade head`): `cars.post_fields`, `source`, `tg_channel`, `tg_message_id`, `source_text`.
