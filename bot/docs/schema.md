# База данных (data/app.db)

SQLite, один файл, единственный источник правды. Схема и миграция — в `db.py` (`SCHEMA`, `db.init()` создаёт таблицы
и при первом запуске переносит `sources.json` и `agents.json`). Режим WAL: бот, `refresh.sh` и будущая админка могут
писать одновременно. Все даты — ISO 8601 UTC (`2026-10-19T12:00:00+00:00`). Списки и словари хранятся как JSON-строки
(`db.row()` раскодирует их).

Для админки и Google Sheets: читать/писать через `db.tx()` и функции `core.py` (там бизнес-правила: статусы, история
лидов, решения модерации). Прямые `UPDATE` в обход `core` — только для полей без правил (например, `sources`, `agents.name`).

## listings — объявления

| Поле | Что |
|---|---|
| `id` | id из ingest: `tg123` (канал агентства), `ss…`, `mh…`, `fb…`, `tg_<канал>_<пост>` |
| `status` | `published` — видят клиенты; `pending_review` — ждёт агента; `rejected` — агент/правило отсеяли; `gone` — пропало из свежей выгрузки |
| `status_reason` | почему pending/rejected (сигналы агента из ingest, «ИИ: …», «отклонено агентом») |
| `decision`, `decided_by` → agents, `decided_at` | решение человека (`approved`/`rejected`); переживает повторные синхронизации; `decided_by` пусто — решение из админки |
| `review_sent_at` | когда карточка проверки ушла агентам (бот шлёт только `NULL`) |
| `source` | `agency`, `ss.ge`, `myhome.ge`, `tg:<username>`, `fb:<key группы>` |
| `owner_id` | автор/аккаунт на источнике |
| `urls`, `dup_sources` | JSON: ссылки на все копии объявления и источники дублей — **только для агентов** |
| `price_usd`, `area`, `floor`, `bedrooms`, `address`, `district`, `lat`, `lng`, `geo_precision` | параметры и точка на карте |
| `photos` | JSON: локальные пути к фото (`raw/img/…`) |
| `description_ru` | чистое описание для клиента (от Codex при индексации) |
| `description_en`, `district_en` | перевод для англоязычных клиентов, делается при первом показе и сохраняется |
| `pets`, `renovation`, `style`, `light`, `furniture`, `features` (JSON), `impression`, `looks` | профиль от Codex |
| `agent_reasons` (JSON), `agent_suspect` | сигналы «это агент» из ingest |
| `details` (JSON) | факты карточки в формате канала (`facts.py`, README «Карточка в формате канала»), таблица ниже; добавлено 21.10.2026, `db.init()` само добавляет столбец в старую базу |
| `text` | исходный текст поста (с телефонами — только агентам) |
| `first_seen`, `last_seen`, `updated_at` | когда впервые/последний раз пришло в выгрузке, когда менялось |

`details` — ключи необязательны, нет ключа = неизвестно. Коды — латиницей, подписи ru/en в `facts.LABELS` / `facts.AMENITIES`:

| Ключ | Что | Пример |
|---|---|---|
| `metro_ru`, `metro_en` | станция метро | `Площадь Свободы`, `Liberty Square` |
| `address_ru`, `address_en` | улица и дом (клиент видит) | `ул. Ивана Мачабели, 9` |
| `headline_ru`, `headline_en` | короткое описание, строка ⭐️ | |
| `district_en` | район по-английски | `Sololaki` |
| `property` | `apartment` / `house` / `commercial` | |
| `building` | `new` / `old` / `historical` / `construction` | |
| `condition` | `new` / `good` / `mixed` / `old` / `white_frame` / `needs_repair` / `retro` | |
| `floors` | этажей в доме (этаж — столбец `floor`) | `9` |
| `heating` | `central` / `gas` / `electric` / `none` | |
| `bath`, `bathrooms` | `shower` / `bath` / `both`; число санузлов | |
| `yes`, `no` | удобства есть / точно нет, коды `facts.AMENITIES` (balcony, terrace, courtyard, wifi, tv, stove, oven, conditioner, elevator, parking…) | `["balcony","wifi"]` |
| `tenants` | сколько жильцов | `1-3` |
| `pets` | `allowed` / `by_agreement` / `not_allowed` (столбец `pets` — то же по-русски, для подбора) | |
| `terms` | сроки аренды, месяцев | `[6, 12]` |
| `deposit_usd`, `commission` | депозит в $, комиссия как в посте (показывается только у `agency`) | `1000`, `0%` |
| `exclusive` | эксклюзив агентства | `true` |
| `videos` | `[{path, w, h, duration}]`, локальные mp4 ≤ `VIDEO_MAX_MB` (`raw/video/…`; видеоссылки — `fetch_videos.py`), в альбом | |
| `video_msg` | агентство: id сообщения с видео в альбоме поста (t.me/s или Telethon); бот берёт по нему `file_id` (таблица `channel_videos`) | `540658` |
| `video_url`, `tour_url` | внешние видео / 3D-тур; кнопка клиенту только если домен не выдаёт источник | `https://youtu.be/…` |

Правила синхронизации (`db.sync`, вызывается `python db.py sync` после `ingest.py`):
- `listings.json` + `raw/dropped_agents.json` → upsert по `id`;
- `agency` → всегда `published`;
- есть **точный** сигнал агента (один телефон в 3+ объявлениях, один аккаунт 3+, FB: имя/страница агентства,
  FB: один автор 2+ квартиры) → `rejected`;
- любой другой сигнал (одинаковый текст, перепост фото, «ИИ: …», `agent_suspect` у собственников) → `pending_review`;
- настройка `moderate_all = 1` → все `tg:*` и `fb:*` тоже `pending_review`;
- есть `decision` человека → он важнее правил;
- всё, чего нет в свежей выгрузке, → `gone` (если выгрузка меньше половины живой базы — считается сломанной, `gone` не ставится;
  `--partial` отключает этот шаг).

## clients — клиенты (любой канал)

| Поле | Что |
|---|---|
| `channel`, `external_id` | `tg` + Telegram user id (потом `web` + сессия, `whatsapp` + телефон); уникальная пара |
| `chat_id`, `username`, `name`, `phone` | контакты |
| `lang` | `ru` / `en`: по тексту последнего сообщения (кириллица/латиница), иначе по языку Telegram |
| `state` | JSON текущего запроса: `msgs` (тексты), `images` (пути к фото клиента), `pin`/`pin_label`/`pin_radius` (точка), `near` (кэш распознанного места), `query` (кэш условий для hybrid: бюджет, спальни, районы, животные, стиль), `shown` (показанные id в этом запросе) |
| `created_at`, `updated_at` | |

## reactions — лайки и дизлайки

`client_id` → clients, `listing_id` → listings, `reaction` (`like`/`dislike`), `created_at`. Одна запись на пару
клиент+объявление. Используется в подборе («похожие на понравившиеся, не похожие на отвергнутые»).

## leads — заявки

| Поле | Что |
|---|---|
| `client_id`, `listing_id` | кто и что лайкнул (один лид на пару) |
| `status` | `new` → `in_work` (взять в работу) → `viewing` (просмотр назначен) → `rented` (сдана) / `refused` (отказ) |
| `agent_id` → agents | первый агент, нажавший кнопку статуса |
| `request` | запрос клиента на момент лайка |
| `created_at`, `updated_at` | |

`lead_events` — история: `lead_id`, `status`, `agent_id`, `at` (первая запись `new` при создании).

## agents — агенты

`tg_id` (Telegram id, он же личный чат), `name`, `username`, `active` (0 — не получает лиды), `created_at`.
Регистрация: `/agent <AGENT_CODE>` в боте; в групповом режиме участник группы становится агентом при первом нажатии;
в админке — по Telegram ID или по @username (`tg_id` пустой, пока агент впервые не напишет боту: `core.agent` привяжет id по username).

## sources — источники

| Поле | Что |
|---|---|
| `kind` | `tg_agency`, `tg_competitor`, `fb_group` |
| `key` | username канала / key группы (уникально; в listings → `tg:<key>` / `fb:<key>`) |
| `name`, `url`, `lang` | для людей |
| `options` | JSON для сборщиков: `want`, `photos`, `scan`, `caption_marker`, `id`… |
| `enabled`, `position` | выключенные не попадают в `sources.json`; порядок |

Сборщики пока читают `sources.json`: `python db.py export-sources` пишет его из базы (делает `refresh.sh`).
Значения по умолчанию сборщиков — в settings `telegram_defaults` / `facebook_defaults` (JSON).

## settings — настройки (key → value)

| key | По умолчанию | Что |
|---|---|---|
| `moderate_all` | `0` | `1` — все объявления конкурентов и FB проходят проверку агентом |
| `lead_target` | `agents` | `agents` — лиды и проверка каждому агенту в личку; `group` — в `group_chat_id` |
| `group_chat_id` | | id группы агентства (ставится командой `/leads_here` в группе) |
| `review_batch` | `5` | сколько объявлений на проверку бот шлёт за раз (каждые 10 мин) |
| `search_mode` | `llm` | `llm` — весь каталог в Codex; `hybrid` — фильтры в коде + локальные эмбеддинги, в Codex ~20 строк (README «Гибридный поиск») |
| `final_llm` | `on` | только для `hybrid`: `off` — топ-5 по похожести без второго вызова Codex, причина собирается из профиля |
| `telegram_defaults`, `facebook_defaults` | из `sources.json` | лимиты сборщиков |

## Векторы (не в SQLite)

`vectors.npz` рядом с базой (`data/vectors.npz`), пишет `embed.py`: `ids`, `keys` (отпечаток: модель + фото + профиль),
`img` (среднее по фото), `txt` (профиль), `photo` + `owner` (каждое фото и номер объявления). float32, нормированы.

## searches — поиски клиентов

`client_id`, `results` (сколько показали), `at`. Одна строка на каждый вызов `core.search` — для статистики «запросов» в админке.

## tg_files — загруженные в Telegram файлы

`path` (локальный файл), `file_id`, `size`. Видео объявления загружается в Telegram один раз, дальше альбом шлёт его по
`file_id` (если файл не изменился — сверяется размер). Только для Telegram-адаптера.

## channel_videos — видео канала агентства по file_id

`chat` (username канала, `AGENCY_CHANNEL`), `message_id`, `media_group_id` (альбом Telegram; `post:<id поста>` у
видео, полученного пересылкой), `file_id` (`NULL` — сообщение без видео, `''` — пересылка не удалась, пост удалён),
`at`. Пишет `bot.py`: каждое сообщение канала из `channel_post`/`edited_channel_post` и backfill старых постов
(`forwardMessage` по `details.video_msg` → `file_id` → удалить копию). Видео объявления `tg<N>` = строка с `file_id`
среди: сообщение `N`, его альбом (`media_group_id` сообщения `N`) или `post:N`. Только для Telegram-адаптера.

## messages — копии карточек в Telegram

`kind` (`lead`/`review`), `ref` (id лида/объявления), `chat_id`, `message_id`. Нужна, чтобы при смене статуса
обновить карточку у всех агентов сразу. Только для Telegram-адаптера.

## journey_answers — ответы на макеты пути клиента

`created_at`, `name` (необязательное «Ваше имя»), `payload` (JSON `{"name", "answers": [{"id", "question", "answer"}]}`),
`ip_hash` (HMAC адреса отправителя, сам IP не хранится; нужен для лимита 20 отправок в час). Пишет `admin.py` из
публичной формы `POST /journey/answers` на странице `/journey`; список в админке «Ответы» (`/answers`).
