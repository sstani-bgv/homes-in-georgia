# Homes in Georgia Platform — architecture & contract

Single source of truth for everyone working in this repo. If code and this doc disagree, fix one of them.

Sources: `docs/user-journey.txt` (the journey agreed with David — the spec for funnels, screens, buttons, SLAs),
`docs/DEVELOPMENT_PLAN.md` (order of work), `docs/legacy/apps-script.js` (the old Google Apps Script backend),
`data/legacy-sheets/*.csv` + `SCHEMA_MANIFEST.json` (31 Google Sheets, synthetic rows only),
`bot/` (Stanislav's prototype bot, SQLite), `docs/legacy/crm/` (current CRM single-file app + tokens.css),
`docs/legacy/repos/` (other legacy repos). **`docs/legacy/` contains leaked secrets — it is git-ignored, never copy
tokens from it.**

## 1. Layout

```
backend/            Python 3.12, FastAPI, SQLAlchemy 2 (sync, psycopg 3), Alembic, PostgreSQL 16
  app/
    config.py       pydantic-settings, reads .env (ALL secrets/ids live in env, nothing hardcoded)
    db.py           engine, SessionLocal, Base, get_db
    models/         SQLAlchemy models (one module per area)
    schemas/        Pydantic v2 request/response models
    api/            FastAPI routers, mounted under /api
    services/       domain logic (funnels, commissions, SLA, dedup) — used by API, bot and worker
    telegram/       Bot-API client + every outgoing Telegram message/keyboard (notify.py) — owned by the bot track
    worker.py       scheduler loop: due `jobs` rows → handlers (SLA reminders, T14, contract expiry, ...)
    importers/      sheets_csv.py: Google Sheets CSV export → Postgres (idempotent)
    main.py         FastAPI app: /api, /health, serves frontend build (/) and legacy tools (/tools/*)
  alembic/          migrations (single source of the schema, bot tables included)
  tests/            pytest, runs against TEST_DATABASE_URL
bot/                the prototype bot (search, ingest, fetchers) ported to Postgres + new journey handlers
frontend/           React 18 + Vite + TypeScript: CRM (desktop) and agent/operator panel (Telegram Mini App, mobile)
deploy/             docker-compose.yml, Dockerfiles, nginx/caddy example
docs/
.env.example        every variable, documented, no real values
```

Processes (docker-compose services): `db` (postgres), `api` (uvicorn app.main:app), `bot` (python -m bot.run —
polling), `worker` (python -m app.worker), `refresh` (bot/refresh loop: fetchers + ingest every REFRESH_HOURS).
The frontend is built into `frontend/dist` and served by `api` (single domain, no CORS in prod).

Python imports: `backend/` and repo root are on `PYTHONPATH`; the bot imports `app.services.*`, `app.models`,
`app.telegram.notify` directly (same DB, same domain logic — no HTTP between bot and backend).

## 2. Configuration (.env)

```
DATABASE_URL=postgresql+psycopg://homes:homes@db:5432/homes
TEST_DATABASE_URL=...
SECRET_KEY=                      # JWT signing
JWT_TTL_HOURS=72
PUBLIC_URL=https://crm.example   # used for Mini App links (WebApp buttons)
BOT_TOKEN=                       # @HomesinGeorgia_bot
BOT_USERNAME=HomesinGeorgia_bot
TG_API_BASE=https://api.telegram.org   # overridable for tests (fake Bot API)
OPERATORS_CHAT_ID=               # operators group (B1)
OPERATORS_TOPIC_ID=              # optional forum topic
WORK_GROUP_CHAT_ID=              # agency work group with topics (old Apps Script MANAGER/NEWS chat)
TOPIC_PHOTO= TOPIC_SHOWING= TOPIC_STICKERS= TOPIC_DEAL= TOPIC_T14= TOPIC_NEWS= TOPIC_LISTINGS= TOPIC_GENERAL= TOPIC_DAILY=
AGENCY_CHANNEL=rent_tbilisi_ge
AGENCY_CHANNEL_ID= DEALS_CHANNEL_ID= NEWS_CHAT_ID= TOPIC_DEAL_EXTRA= TOPIC_PHOTO_QUEUE=   # inbound updates (services/inbound.py)
PUBLISH_CHAT_ID= PUBLISH_TOPIC_ORIGINALS= PUBLISH_TOPIC_OWNER= PUBLISH_TOPIC_ALBUM=   # channel publish: chat + topics
SUPERADMIN_TG_IDS=               # comma-separated; bootstrap superadmins
ADMIN_BOOTSTRAP_LOGIN= ADMIN_BOOTSTRAP_PASSWORD=     # first desk login, created on startup if no superadmin
LLM_PROVIDER=openai  OPENAI_API_KEY=  OPENAI_BASE_URL=  LLM_MODEL=
APIFY_TOKEN=  GOOGLE_SA_JSON=  SHEET_ID=           # optional integrations
TZ=Asia/Tbilisi
SLA_OPERATOR_TAKE_MIN=5  SLA_AGENT_SLOTS_MIN=30  SLA_SLOTS_TO_CLIENT_MIN=60  CLIENT_PROMISE_MIN=30
CALL_RETRY_HOURS=2  VIEWING_REMINDER_MIN=60  POST_VIEWING_SURVEY_MIN=60  T14_DAYS=14  CONTRACT_REMIND_DAYS=30,7,1
MEDIA_DIR=/app/data/media
MEDIA_STORAGE=local              # s3: CRM media in an S3-compatible bucket (backend/app/storage.py)
S3_ENDPOINT= S3_BUCKET= S3_ACCESS_KEY= S3_SECRET_KEY= S3_REGION=us-east-1 S3_PUBLIC_URL=
```

## 3. Roles & auth

Roles: `superadmin` (David), `admin` (sysadmin), `operator`, `agent`, `recruit`. Clients are NOT users
(they live in `clients`).

Auth = JWT bearer (`Authorization: Bearer <token>`), payload `{sub: user_id, role}`.
- `POST /api/auth/telegram-webapp {init_data}` — Mini App: verify initData HMAC with BOT_TOKEN, max age 24h,
  find active user by tg_id (else 403 `not_registered`). Also upserts username/name.
- `POST /api/auth/telegram-login {id, first_name, username, photo_url, auth_date, hash}` — Telegram Login Widget.
- `POST /api/auth/login {login, password}` — desk users (Platform_Desk_Users), bcrypt hashes.
- `GET /api/auth/me` → `UserOut`.
Permissions (enforced in API, `require_roles(...)`):
- owner phone of a property: visible to the agent who took it + operator/admin/superadmin (journey C2a; open question C3).
- client contacts in a lead: visible to agent only after `contacts_shared_at` is set; always to operators/admins.
- agents/recruits see only their own properties/leads/deals/reports in "my" endpoints; lists of "new listings" are shared.

## 4. Database (PostgreSQL). All timestamps `timestamptz` (UTC) unless noted as legacy TEXT.

### Bot catalog tables (ported 1:1 from bot/db.py SQLite schema, kept TEXT/INTEGER types so bot SQL keeps working)
`settings(key PK, value)`, `sources`, `listings`, `clients`, `reactions`, `leads`, `lead_events`, `searches`,
`tg_files`, `channel_videos`, `messages`, `journey_answers` — same columns as in `bot/db.py`, with these changes:
- `agents` table is replaced by `users` (below). FKs `decided_by`, `agent_id` → `users.id`.
- `clients` + columns: `profile TEXT NOT NULL DEFAULT '{}'` (JSON, see 4.3), `profile_done INTEGER NOT NULL DEFAULT 0`,
  `t14_optin` not needed.
- `leads`: see 4.3 (extended). `lead_events` + `kind TEXT`, `note TEXT`, `actor_id` (rename of agent_id kept as
  `agent_id` for bot compat + new `actor_id`). `messages.kind` CHECK dropped (free text: lead, review, group_lead,
  slots, property).
- timestamps in these legacy tables stay ISO TEXT (bot code compares strings); new columns use timestamptz.

### 4.1 People
`users`: id PK, tg_id BIGINT UNIQUE NULL, username TEXT, name TEXT NOT NULL, phone, role TEXT NOT NULL
CHECK in (superadmin, admin, operator, agent, recruit), level INT NULL (50/70/90 commission level for agents),
mentor_id FK users NULL (recruit → mentor agent), districts JSONB default [] (agent coverage), is_active BOOL default
true, login TEXT UNIQUE NULL, password_hash NULL, photo_url, birth_date DATE NULL, exclusive_deals INT default 0
(Agent_Counters), plans JSONB default {"photo":15,"showing":15,"stickers":15,"rental":15} (Counters plans),
legacy_id TEXT, created_at, last_login_at.

Agent level rule (Apps Script getAgentLevel): exclusive_deals <11 → 50, <101 → 70, else 90; `next_level_at` derived.

### 4.2 Funnel 1 — objects
`owners`: id, name, phone, phone_norm (normP from apps script: strip spaces,+,-,(),leading 995 and 0) INDEX,
address, address_norm, link, is_agent BOOL default false (C2b: phone known as an agent), notes, created_by FK users,
created_at. (Owner_Base)

`properties`: id PK, listing_id TEXT FK listings.id NULL UNIQUE, source_type TEXT (ss, myhome, facebook, telegram,
agency_channel, direct, referral, import, other), source_url, source_external_id, status TEXT NOT NULL:
```
new → taken → (call_failed ↺) → owner_confirmed → shoot_scheduled → media_uploaded → review → published
                              ↘ agent_marked (listed by an agent; stays in base with mark)   review → returned → media_uploaded
any → archived ; published → rented
```
owner_id FK owners, agent_id FK users (who took it; locked for others), taken_at, call_attempts INT, next_call_at,
shoot_at, address, district, cadastral_code, price NUMERIC, currency default 'USD', bedrooms, rooms, area, floor,
total_floors, description, is_exclusive BOOL, checklist JSONB (rooms → bool), ai_flags JSONB
(owner|agent_suspect|duplicate + reasons, copied from listings), duplicate_of FK properties NULL,
dup_check TEXT (added|exists — C3 message), review_comment, reviewed_by FK users, reviewed_at, published_at,
post_url, post_message_id, created_at, updated_at, text_embedding / photo_embedding vector(768) (pgvector, SigLIP,
HNSW cosine; not ORM-mapped, written by `app/embeddings.py`).
`property_media`: id, property_id FK, kind (photo|video), path (storage key, `properties/<id>/<uuid>.<ext>`), room TEXT NULL, position INT, uploaded_by, created_at.
The object is the main entity (one real flat); ads and our posts attach to it (migration 0007):
`property_listings`: id, property_id FK CASCADE, listing_id FK listings UNIQUE (one ad → one object), source, url,
first_seen_at, last_seen_at, created_at. properties.listing_id stays the "origin" ad; `services.properties.link_listing`.
`property_publications`: id, property_id FK CASCADE, channel TEXT (only `tg` now; site/ig/fb later, no migration),
status (draft|published|removed), url, message_id, published_at, removed_at, created_at. Written by publish /
`record_publication`); post_url/post_message_id on properties are kept for compat.
`channel_posts.property_id` FK NULL: an agency channel post matched to our publication by link (exact only).
Auto-matching (migration 0008, `services/matching.py`): every new listing (bot `db.sync` → `matching.process`) gets a
verdict per object: same (link / cadastral / 2+ dHash photos / owner phone + address) → linked to the object itself;
similar (district + bedrooms + floor + area ±10%, or text embedding ≥ 0.92) → `listing_matches` (listing_id,
property_id, score, reasons JSONB, status pending|confirmed|rejected, decided_by, decided_at), decided by an operator
in «Новые объявления». `listings.photo_hashes` = the ingest's dHash per photo. Exclusive objects: a live outside ad
(`property_listings` not gone, not `removed_checked_at`) is a conflict: red badge, event, task to the agent, and
publish / approve answer 409 `exclusive_conflict` until an operator presses «Проверено: снято» (manual check: ss.ge /
myhome block server requests, so an HTTP probe can't tell removed from blocked).

Generic history: `events`: id, entity TEXT (property|lead|deal|user|owner), entity_id TEXT, action TEXT,
actor_id FK users NULL, actor_label TEXT (e.g. 'bot', 'client'), data JSONB, at timestamptz. Every status change
writes one. CRM "История" tabs read it.

### 4.3 Funnel 2 — clients & leads
`clients.profile` JSON: `{residents: "1"|"2"|"3"|"4+", pets: ["none","cat","dog","other"], terms: ["1-3","6","12","12+"],
budget_max: int|null, budget_min: int|null, bedrooms: [0,1,2,3,4], districts: [..]|["any"], pin: {lat,lng}|null,
radius_km: float|null, move_in: "YYYY-MM-DD"|null, photos: n}`.

`leads` (bot table, extended): id, client_id, listing_id, property_id NULL, status TEXT:
```
new            client pressed ❤️ (A3), posted to operators group, SLA operator_take (5 min)
operator_taken an operator pressed "Взять" in group (B1) → operator_id
agent_assigned operator assigned agent (B3) → agent_id, SLA agent_slots (30 min) (C6)
slots_sent     agent entered free slots (C7) → bot sent them to client (A4a), SLA client pick none
time_chosen    client picked slot (A4b) → contacts_shared_at=now, contacts sent to agent (C8/B5), reminders 1h before
viewing_done   viewing time + POST_VIEWING_SURVEY_MIN passed → outcome questions to client & agent (A5a/C9a)
deal_pending   agent outcome "rented" → deal form (C9b) → deals.status=pending
rented         operator confirmed deal (B6/C9c)
refused        "не подошло" (reason saved, A5b) or cancelled by operator
thinking       "думаю" → remind tomorrow
```
Columns: operator_id FK users, agent_id FK users, request TEXT, sla_stage TEXT NULL (operator_take|agent_slots|
slots_to_client), sla_due_at timestamptz NULL, viewing_at timestamptz, contacts_shared_at timestamptz,
client_outcome TEXT (rented|refused|thinking), agent_outcome TEXT (rented|refused|thinking|reschedule),
refusal_reason TEXT, client_note TEXT, deal_id FK deals NULL, created_at/updated_at (TEXT legacy) + created_ts timestamptz.
`lead_slots`: id, lead_id FK, starts_at timestamptz, created_by FK users, chosen BOOL default false, created_at.

SLA colour for CRM cards: green (> 40% of window left), yellow (≤ 40%), red (overdue).

### 4.4 Deals, evaluations, reports (Google Sheets replacement)
`deals`: id, lead_id NULL, property_id NULL, agent_id FK users, recruit_id NULL, deal_type (rent|sale),
sub_type (long|short|daily|commercial) NULL, is_exclusive, is_rerented, price NUMERIC, commission_pct NUMERIC,
commission_total, platform_amount, agent_amount, recruit_pct, recruit_amount, client_src TEXT, client_src_amount,
agent_level INT, agent_deal_num INT, cadastral, ad_link, address, metro, client_name, client_phone, client_tg,
client_birth DATE, owner_name, owner_phone, owner_birth DATE, rent_from DATE, rent_to DATE, status
(pending|confirmed|cancelled), confirmed_by, confirmed_at, t14_date DATE, t14_status (pending|done|skipped),
month TEXT ("M/YYYY"), source (crm|bot|import|telegram), raw JSONB, tg_ref TEXT UNIQUE NULL ("<chat>:<message>" of a
parsed #Rented message), created_at.
Commission (saveRentalWithT0): comm = commission_total or round(price*pct/100); platform = given or
comm*(100-level)/100; agent = comm - platform - recruit_amount - client_src_amount(10% of comm if client_src).
On confirm: `users.exclusive_deals += 1` if exclusive, level recomputed, `#Rented` text posted to TOPIC_DEAL,
jobs: T14 at rent_from+14d, contract reminders at rent_to-30/7/1 days.

`evaluations`: id, deal_id FK, kind (t0|t14), author (agent|client), h2o_client INT 0-3, h2o_owner, h2o_process,
result (clean|dirty) NULL, changes TEXT, complaints BOOL, complaints_text, comment, created_at.
Low score (any h2o ≤ 1) or complaints → notify operators (A6).

`reports`: id, kind (photo|showing|stickers|daily|general|makefoto|calc|assigned_showing), author_id FK users,
recruit_id, mentor_id, date DATE, data JSONB (all form fields), created_at. Counters per recruit/month are computed
(no counters table). Each report posts its text to the matching work-group topic (texts as in apps-script).

`ticker_messages`: id, text, status (active|deleted), created_at. `news`: id, date, text, message_id.
`addresses`: id, district, address, ad_link, is_active. `channel_posts`: id, message_id UNIQUE, date, agent_name,
district, price, rooms, building, is_exclusive, link, raw TEXT, status (active|rented|archived), property_id NULL (Listings +
Active_Listings sheets / channel topic parser).

`jobs` (durable scheduler): id, kind TEXT, ref TEXT, due_at timestamptz, payload JSONB, done_at NULL, attempts INT,
last_error, created_at; INDEX (done_at, due_at). Kinds: `lead_sla` (ref lead id + stage), `call_retry` (property),
`viewing_reminder` (lead), `post_viewing_survey` (lead), `thinking_followup` (lead), `t14_client` (deal),
`t14_agent` (deal), `contract_expiry` (deal, payload days), `review_batch`. Creating a job for (kind, ref, stage) cancels
previous undone ones (`services.jobs.schedule(kind, ref, due_at, payload, replace=True)`).

## 5. Services (Python API used by API routers, bot and worker) — `backend/app/services/`

All functions take `db: Session` first, commit themselves, raise `app.services.errors.DomainError(code, message)`
(API maps to 400/403/404/409), write `events`, schedule `jobs`, and call `app.telegram.notify.*` (which must never
raise — it logs failures).

`leads.py`
- `create_from_like(db, client_id, listing_id) -> Lead` (requires a published agency listing or a published listing linked to a published property, including repeat likes; idempotent per client+listing; status new; notify.lead_new_group; SLA operator_take)
- `operator_take(db, lead_id, operator: User) -> Lead` (409 `already_taken` if someone else)
- `assign_agent(db, lead_id, agent_id, actor: User) -> Lead` (notify.lead_assigned_agent; SLA agent_slots)
- `recommend_agents(db, lead_id) -> list[dict]` (agent, score, reasons: owns property / district match / load)
- `add_slots(db, lead_id, starts: list[datetime], actor) -> Lead` (status slots_sent; notify.slots_to_client; SLA cleared)
- `choose_slot(db, slot_id, client_id) -> Lead` (status time_chosen, viewing_at, contacts_shared_at; notify.slot_confirmed_client, notify.contacts_to_agent; jobs viewing_reminder, post_viewing_survey)
- `client_own_time(db, lead_id, text)`, `client_late(db, lead_id)`, `request_reschedule(db, lead_id, by)` 
- `set_outcome(db, lead_id, by: 'client'|'agent', outcome, reason=None, actor=None)`
- `cancel(db, lead_id, actor, reason)`, `reassign`, `history(db, lead_id)`
- `board(db, user) -> dict[status, list[LeadCard]]`
`properties.py`
- `new_listings(db, q)` (listings not yet linked to a property, owner-like, newest first, with ai flags)
- `take(db, listing_id|None, agent, manual_fields=None) -> Property` (409 `taken_by_other`)
- `call_result(db, pid, agent, reached: bool)` (not reached → call_failed + job call_retry +2h)
- `owner_or_agent(db, pid, agent, is_owner: bool, owner_name, owner_phone)` (agent → status agent_marked, owners.is_agent=true)
- `schedule_shoot(db, pid, agent, shoot_at) -> (Property, dup_check)` (owner dedup by phone_norm/address_norm → 'added'|'exists')
- `upload_media(db, pid, files, room)`, `set_checklist`, `submit_for_review(db, pid, agent)`
- `review(db, pid, operator, approve: bool, comment)` (approve → publish; return → agent notified)
- `publish(db, pid, actor, text=None, media_ids=None, channel="tg", comment=None)` (stage 5, the card's post generator: caption = edited text or post_text() in the agency format, the picked media in order; publishing a `review` object approves it; sends via notify.publish_post, records the publication, notifies the agent C5; Telegram failure → 502 `publish_failed`, nothing changes; dev without BOT_TOKEN → dry run recorded without a link)
- the agent's media and checklist are read-only after `/submit` (409 `submitted`) until the operator returns the object
- stage 6.5: `owners.agent_id` = the agent who brought the owner (all his objects show that agent). Binding a free owner
  is automatic (call step, card phone fix, re-take); an owner of another agent → 409 `owner_taken` «Собственник закреплён за
  #Tag» for agents, staff may link anyway (event `owner_agent_override`). `transfer_owner(db, owner_id, to_agent_id,
  by_user, reason)` (staff, `POST /owners/{id}/transfer`): owner + his live objects of the old agent move, `owner_transferred`
  events. An owner who refused is released. `leads.recommend_agents` puts the owner's agent first («его собственник»).
- `post_text(db, p)`: port of the old generator's buildRentPost/buildSalePost from the object + `properties.post_fields`
  (JSONB: deal, prop_type, metro, building, design, condition, heating, bathrooms, pets, amenities[], periods[], tenants,
  deposit, sale_price, headline; vocabulary POST_ONE/POST_MANY/POST_NUM), `📍[address](maps link)` (notify.post_html
  makes it a link), the agent's `users.agent_tag` (unique, `GET /users/agent-tag-suggestion`; inbound.resolve_agent
  looks it up before the `agent_tags` setting)
- stage 6: `properties.verified_at` = the first operator publish (review approve). After it an agent's change of
  `LOCKED_AFTER_VERIFY` (owner name/phone and so the owner binding, address, cadastral code, ad link `source_url`, exclusive,
  price/currency, post_fields deposit/sale_price) is not applied: `PATCH /properties/{id}` applies the rest and returns
  `change_request` («Запрос оператору», table `change_requests`: entity, entity_id, kind edit|archive|transfer_owner|delete,
  payload {field: {old, new}}, reason, status pending|approved|rejected, decided_by/at, decision_comment). Agent owner
  transfer: `POST /properties/{id}/transfer-request`. Queue: `GET /change-requests?status=pending` (staff: all, agent: own),
  `POST /change-requests/{id}/decide {approve, comment}` (staff; reject needs a comment) → applied through apply_update /
  archive / transfer_owner, events `change_requested` / `change_request_approved|rejected`, notify.change_request_new (operators)
  and change_request_decided (the agent). Archive: `POST /properties/{id}/archive {reason, comment, duplicate_of}`, reasons
  `models.ARCHIVE_REASONS` (other → comment, duplicate → duplicate_of required), fields archive_reason/archive_comment/archived_at;
  refused → owner_refused, rented-self → rented_by_owner, confirmed deal → rented_by_us. Objects are never deleted.
- `check_duplicate(db, phone, address, url, cadastral)` (port of apps-script checkProperty + legacy lib-repository rules)
- stage 7: the deal form picks its object: `GET /properties/deal-search?q=|pid=&deal_type=&lead_id=` (address, #id,
  cadastral, owner phone; all agents' objects; another agent's one → `blocked` «Объект закреплён за #Tag» and no data).
  `deals.create` with `property_id` takes `properties.deal_fields` (address, cadastral, metro, exclusive, price/sale price;
  ad link + owner only if the user sees the owner) over the form; an agent's deal on another agent's object → 409
  `object_taken` (the object of his own lead is allowed). `new_object: true` («Объекта нет в базе»): `matching.same_as`
  (same link / cadastral / phone+address) → 409 `object_exists` «…#id… Выберите его из базы»; else `take(commit=False)` makes
  an object `source_type=deal` with the owner bound to the agent; `needs_check` (no verified_at) blocks `confirm` (409
  `object_unverified`) until `POST /properties/{id}/verify` (staff, sets verified_at). `GET /deals/{id}` → `property`.
`deals.py`: `calc(...)`, `create(db, data, actor)`, `confirm(db, deal_id, actor)`, `cancel`, `rented_text(deal)`
`evaluations.py`, `reports.py` (save + topic post + counters), `users.py` (level logic, promote recruit → agent),
`inbound.py` (Telegram work group / channel → channel_posts, news, owners + makefoto reports, pending deals; bot/inbound.py
feeds it), `analytics.py` (dashboard metrics, listings_stats / weekly_listings: deals count, platform commission, active objects, leaderboards, funnel numbers,
response times per agent/operator), `jobs.py` (schedule/cancel/due), `settings.py`.

## 6. Telegram contract — `backend/app/telegram/` (bot track owns it; others only call it)

`client.py`: sync httpx Bot-API client (`call(method, **params)`) using BOT_TOKEN/TG_API_BASE; never raises.
`notify.py` — every outgoing message (RU/EN by client.lang; staff in RU). Signatures (all `-> None`):
```
lead_new_group(lead)                 B1 card in operators group, button "✋ Взять" (lg:take:<id>)
lead_taken_group(lead, operator)     edits the group card(s): "✋ Взяла <name>", button "Назначить агента" (lg:assign:<id>) visible
lead_assigned_agent(lead)            C6 to agent: object + client profile without contacts, button "🕒 Ввести время" (Mini App link /m/leads/<id>)
lead_client_ack(lead)                A3: edits the client's card text: "✅ Передано оператору · ответим в течение N минут", button "❤️ В избранном"
slots_to_client(lead, slots)         A4a: photo + "Район · N спальни · $price" + slot buttons (ls:pick:<slot_id>) + "Другое время" (ls:own:<lead_id>)
slot_confirmed_client(lead, agent)   A4b: address, agent name/phone, "💬 Написать агенту" url, "📅 Добавить в календарь" (url to /api/public/ics/<token>), "Опаздываю" (lv:late:<id>), "Перенести" (lv:resched:<id>)
contacts_to_agent(lead)              C8
viewing_reminder(lead)               1h before, to client and agent
post_viewing_survey(lead)            A5a to client (lo:c:<id>:rented|refused|thinking) + C9a to agent (lo:a:<id>:rented|refused|thinking|reschedule)
refusal_reason_ask(lead)             A5b reasons buttons (lr:<id>:price|location|condition|other)
sla_warning(lead, stage) / sla_overdue(lead, stage)     B4 reminder → escalation to operators group (+ superadmins)
deal_confirmed(deal)                 C9c to agent; #Rented to TOPIC_DEAL
t14_client(deal)                     A6: three questions 0-3 (t14:<deal_id>:<q>:<0-3>) + "Есть проблема"
t14_agent(deal) / contract_expiry(deal, days)
property_call_retry(prop)            C2a reminder
property_review_request(prop)        to operators
property_returned(prop) / property_published(prop)     to agent (C5 with post link)
publish_post(prop) -> (message_id, url) | None   PUBLISH_CHAT_ID (else the agency channel): originals as documents → PUBLISH_TOPIC_ORIGINALS,
                                                 watermarked album + linked caption → PUBLISH_TOPIC_ALBUM, owner contact → PUBLISH_TOPIC_OWNER
report_posted(report)                topic post (texts from apps-script notifyTopic)
```
Callback data prefixes handled by the bot: `lg:` (group), `ls:` (slots), `lv:` (viewing), `lo:` (outcome),
`lr:` (refusal reason), `t14:`, `q:` (questionnaire, bot-local), plus legacy `like:/dis:/more:` etc. from the prototype.

## 7. HTTP API (`/api`, JSON, snake_case). Pagination: `?limit=&offset=` → `{items, total}`.

Auth: see §3. Users: `GET /users?role=&active=`, `POST /users`, `PATCH /users/{id}` (role, level, active, mentor,
districts, plans), `POST /users/{id}/promote` (recruit→agent), `GET /users/{id}/stats`.
Me/cabinet: `GET /me/cabinet` (counters vs plans this month, my deals, level progress, my properties, my leads, upcoming viewings, expiring contracts).
Funnel 1: `GET /listings/new?q=&source=&flag=&known=` (known=1 also shows ads already linked to an object),
`POST /listings/{id}/matches/{property_id}` {same} (staff), `GET /properties/{id}/elsewhere`,
`POST /properties/{id}/listings/{listing_id}/removed` (staff, «Проверено: снято»), `GET /listings/{id}` (agent-only fields: urls, text),
`GET /properties?status=&agent_id=&q=&mine=1`, `GET /properties/board` (by status), `POST /properties` (manual or
`{listing_id}` = Беру в работу), `GET /properties/{id}`, `PATCH /properties/{id}`, `POST /properties/{id}/call`
`{reached}`, `/owner` `{is_owner, owner_name, owner_phone}`, `/shoot` `{shoot_at}`, `/media` (multipart files, room),
`DELETE /properties/{id}/media/{mid}`, `/checklist`, `/submit`, `/review` `{approve, comment}`, `GET /properties/{id}/post`
(staff: `{text, hashtags, channels}` — the caption built from the object), `/publish` `{text?, media_ids?, channel?, comment?}`,
`/archive`, `GET /properties/{id}/history`, `POST /properties/check-duplicate {phone,address,url,cadastral}`.
Owners: `GET /owners?q=`, `POST /owners`, `PATCH /owners/{id}`.
Funnel 2: `GET /leads?status=&agent_id=&operator_id=&q=`, `GET /leads/board`, `GET /leads/{id}` (with client profile,
listing card, slots, history, outcomes; contacts per §3), `POST /leads/{id}/take`, `GET /leads/{id}/recommend-agents`,
`POST /leads/{id}/assign {agent_id}`, `POST /leads/{id}/slots {starts:[iso]}`, `POST /leads/{id}/reschedule`,
`POST /leads/{id}/outcome {outcome, reason}` (agent), `POST /leads/{id}/cancel {reason}`, `POST /leads/{id}/message {text}` (operator → client via bot).
Clients: `GET /clients?q=`, `GET /clients/{id}` (profile, leads, reactions).
Deals: `POST /deals/calc` (preview numbers), `GET /deals?month=&agent_id=&status=`, `POST /deals`, `GET /deals/{id}`,
`POST /deals/{id}/confirm`, `POST /deals/{id}/cancel`, `GET /deals/expiring?days=30`, `GET /deals/find?cadastral=`.
Evaluations: `POST /evaluations` (t0/t14 by agent), `GET /evaluations?deal_id=`.
Reports: `POST /reports/{kind}`, `GET /reports?kind=&author_id=&month=`, `GET /counters?month=` (per recruit vs plan).
Analytics: `GET /analytics/dashboard`, `GET /analytics/funnel?from=&to=`, `GET /analytics/agents`, `GET /analytics/listings`.
Content: `GET/POST/DELETE /ticker`, `GET /news`, `GET /channel-posts?hashtag=&district=&status=&q=&agent_id=&source=&rooms=&exclusive=&price_min=&price_max=`, `GET/PUT /settings` (admin: lead_target, moderation, SLA minutes,
dashboard metrics toggles), `GET/POST/PATCH /sources` (listing sources), `GET /addresses`.
Catalog moderation (bot prototype "Проверка"): `GET /moderation`, `POST /moderation/{listing_id} {approve}`.
Media: `GET /media/{path}` (auth; MEDIA_STORAGE=s3 → 307 to a presigned or S3_PUBLIC_URL link), `GET /listing-photos/{listing_id}/{n}`.
Public (no auth): `GET /public/ics/{token}` (viewing calendar file), `GET /health`.

## 8. Frontend (React) — design system

Brand tokens (docs/design-system.png, docs/legacy/crm/tokens.css): blue `#032D6A` (primary, headers, gradient),
deep blue `#021F4A` (dark backgrounds, gradient end), red `#E3172D` ONLY for plaques/badges/alerts (≤10% of a screen),
white, light grey `#F3F5F9` (card/page bg), grey-blue `#4A5A75` (secondary text). Font: Montserrat
(HG Sans fallback) 400/700/800/900 + Noto Sans Georgian. Labels: Bold 700, uppercase, letter-spacing 14%.
Headings ExtraBold/Black. Proportion ~60% white, 30% blue, ≤10% red. No exclamation marks in UI copy.
Radii 10/16/24, shadows from tokens.css. Light theme first; dark theme optional.

Navigation (keeps the current CRM's structure, restyled): Главная (dashboard + ticker), Объекты (funnel 1 board +
new listings + base), Заявки (funnel 2 board B2 with SLA timers), Сделки (deals, T0/T14, expiring contracts,
calculator), Клиенты, Собственники, Команда (agents/recruits leaderboard, levels, counters), Отчёты (photo, showing,
stickers, daily, general, make-foto), Управление (users, sources, moderation, ticker, settings), Кабинет (me).
`/m/*` = mobile Mini App views for agents (my objects, my leads, slots entry, deal form, reports), auto-login via
Telegram initData. Desktop ≥1100px: sidebar; mobile: bottom nav.
