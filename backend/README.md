# Backend (FastAPI + SQLAlchemy 2 + Alembic, PostgreSQL 16)

Run everything from `backend/` with the shared venv (`../.venv/bin/python`); config is read from the repo-root `.env`.

```
cd backend
../.venv/bin/alembic upgrade head                      # schema (+ default settings rows)
../.venv/bin/uvicorn app.main:app --reload --port 8000 # API on /api, SPA from ../frontend/dist
../.venv/bin/python -m app.worker                      # scheduler loop (every 30 s); --once for a single pass
../.venv/bin/python -m app.importers.sheets_csv ../data/legacy-sheets   # Google Sheets CSV export → Postgres (idempotent)
../.venv/bin/python -m app.seed_demo                   # demo data (Нино / Георгий / Анна ...)
../.venv/bin/python -m pytest -q                       # tests against TEST_DATABASE_URL
```

Desk login after startup: `ADMIN_BOOTSTRAP_LOGIN` / `ADMIN_BOOTSTRAP_PASSWORD` (superadmin, created if none exists).
Demo logins (seed_demo): `nino` / `demo` (operator), `georgiy` / `demo` (agent), `david` / `demo` (superadmin).

## For other tracks

`backend/` and the repo root must be on `PYTHONPATH` (the bot does `from app.services import leads`).

### DB session
```python
from app.db import SessionLocal          # sessionmaker; every service commits itself
from app.models import (User, Client, Listing, Lead, LeadSlot, LeadEvent, Property, Owner, Deal, Evaluation,
                        Report, Job, Event, Setting, Source, Reaction, Message, ChannelVideo, TgFile, Search)
with SessionLocal() as db:
    ...
```
Bot catalog tables keep the bot/db.py columns and TEXT timestamps (`app.services.common.now_text()` gives the same
ISO format as `bot/db.py now()`). JSON columns of these tables are TEXT (`app.services.common.jload/jdump`).
`agents` is gone: use `users` (role agent/recruit; `tg_id`, `name`, `username`, `is_active`).
`messages.kind` is free text (lead, review, group_lead, slots, property). Telegram ids are BIGINT.

### Errors
All services raise `app.services.errors.DomainError(code, message)` with `.code` / `.status`
(`not_found` 404, `forbidden` 403, `already_taken`/`taken_by_other`/`bad_status` 409, others 400).

### `app.services.leads`
```python
create_from_like(db, client_id: int, listing_id: str, request: str | None = None) -> Lead   # idempotent per client+listing
operator_take(db, lead_id, operator: User) -> Lead             # DomainError('already_taken') if someone else
assign_agent(db, lead_id, agent_id: int, actor: User) -> Lead   # also sets operator_id if nobody took it
reassign(db, lead_id, agent_id, actor) -> Lead
recommend_agents(db, lead_id) -> list[{"agent": {...}, "score", "reasons": [...], "active_leads", "recommended"?}]
add_slots(db, lead_id, starts: list[datetime], actor: User | None) -> Lead   # naive datetimes = Asia/Tbilisi; new slots_token,
    # the userbot sends the slot card; not delivered -> DomainError userbot_off (503), lead back to agent_assigned
choose_slot(db, slot_id, client_id: int | None = None) -> Lead              # contacts shared, jobs scheduled
client_own_time(db, lead_id, text) -> Lead      # "Другое время": back to agent_slots SLA, agent re-notified
client_late(db, lead_id) -> Lead                # history only
request_reschedule(db, lead_id, by='client'|'agent', actor=None) -> Lead   # → agent_assigned, slots deleted
set_outcome(db, lead_id, by='client'|'agent', outcome, reason=None, actor=None) -> Lead
    # client: rented|refused|thinking ; agent: rented|refused|thinking|reschedule
    # refusal reasons: price|location|condition|other (call again with reason after refusal_reason_ask)
cancel(db, lead_id, actor, reason) -> Lead
message_from_client(db, lead_id, client_id, text) -> Lead   # the client's DM to the operator (userbot), history only
mark_viewing_done(db, lead_id) -> Lead | None       # used by the worker
history(db, lead_id) -> list[dict]
card(db, lead, user) / detail(db, lead, user) -> dict   # contact visibility applied
contacts_visible(lead, user) -> bool
board(db, user) -> dict[status, list[card]]
sla_color(db, lead) -> 'green'|'yellow'|'red'|None
```
Statuses: new, operator_taken, agent_assigned, slots_sent, time_chosen, viewing_done, deal_pending, rented, refused,
thinking. `lead.sla_stage` ∈ operator_take (new → agent assigned) | agent_slots (assigned → slots sent).

### `app.services.properties`
```python
new_listings(db, q=None, source=None, flag=None, limit=50, offset=0) -> {"items": [...], "total"}
listing_out(listing, full=False) -> dict
take(db, listing_id: str | None, agent: User, manual_fields: dict | None = None) -> Property   # 'taken_by_other'
call_result(db, pid, agent, reached: bool) -> Property        # not reached → call_failed + job call_retry (+2h)
owner_or_agent(db, pid, agent, is_owner: bool, owner_name=None, owner_phone=None) -> Property
schedule_shoot(db, pid, agent, shoot_at: datetime) -> (Property, 'added'|'exists')   # also posts Make Foto report
upload_media(db, pid, files: list[(filename, bytes)], room: str | None, actor) -> list[PropertyMedia]
delete_media(db, pid, mid, actor); set_checklist(db, pid, checklist: dict, actor)
submit_for_review(db, pid, agent) -> Property                 # notify.property_review_request
review(db, pid, operator, approve: bool, comment=None) -> Property   # approve → publish(); else returned
publish(db, pid, actor, text=None, media_ids=None, channel='tg') -> Property  # notify.publish_post(prop), then property_published
post_text(db, prop) -> str              # channel caption
check_duplicate(db, phone=None, address=None, url=None, cadastral=None) -> {"level": none|warn|danger, "results": [...]}
upsert_owner(db, name, phone, address=None, link=None, is_agent=False, actor=None) -> (Owner, created)
out(db, prop, user, full=False) -> dict   # owner phone only for the taking agent and staff
```

### Others
```python
app.services.deals: calc(price, commission_pct=50, commission_total=None, platform_amount=None, agent_level=50,
                         recruit_pct=None, recruit_amount=None, client_src=None, is_rerented=False) -> dict
                    create(db, data: dict, actor) -> Deal ; confirm(db, deal_id, actor) ; cancel(db, deal_id, actor, reason)
                    rented_text(deal, db=None) -> str        # '#Rented' HTML for TOPIC_DEAL (use in notify.deal_confirmed)
app.services.evaluations: create(db, {deal_id, kind:'t0'|'t14', author:'agent'|'client', h2o_client, h2o_owner,
                          h2o_process, result, changes, complaints, complaints_text, comment}, actor) -> Evaluation
                          t14_text(db, ev) -> str
app.services.reports: save(db, kind, data, author) -> Report  # report.data["posts"] = [{"topic", "text"}] for report_posted
app.services.users: by_tg(db, tg_id), level_info(user), effective_level(user), promote(db, uid, level, actor)
app.services.settings: get(db, key, default=None), set(db, key, value), all(db), sla_minutes(db, stage)
app.services.jobs: schedule(db, kind, ref, due_at, payload=None, replace=True), cancel(db, kind, ref)
```

### Notify contract
Services call `app.telegram.notify.<fn>` **after commit** with ORM objects (Lead, Property, Deal, Report, Evaluation,
Client). Job handlers in `app/worker.py` call: `sla_warning(lead, stage)`, `sla_overdue(lead, stage)`,
`property_call_retry(prop)`, `viewing_reminder(lead)`, `post_viewing_survey(lead)`, `t14_client(deal)`,
`t14_agent(deal)`, `contract_expiry(deal, days)`; `thinking_followup` re-uses `post_viewing_survey(lead)`.
`client_late` calls `viewing_update(lead, "late")`. `publish(prop)` sets transient `prop.post_text` and `prop.post_media_ids` before
`publish_post(prop)`. `Report.posts` = `[{"topic", "text"}]` (Telegram HTML, apps-script texts; `general` can post to
several topics), `Report.text` = plain-text version of the same.

## Jobs (worker)
`lead_sla` (ref lead id, payload `{stage, phase: warning|overdue}`; warning at 60 % of the window, overdue at the
deadline), `call_retry` (property), `viewing_reminder`, `post_viewing_survey` (→ status viewing_done),
`thinking_followup`, `t14_client` / `t14_agent` (deal; 10:00 Tbilisi on rent_from + 14 d), `contract_expiry`
(ref `<deal_id>:<days>`, payload `{days}`). Stale jobs (entity moved on) finish with `last_error='stale'`; failures retry
with backoff 2^n min, 5 attempts.

Clients can request only published agency-channel listings or published listings linked to a published funnel-1 property.
The same rule applies to old cards and reopening refused leads; there is no broad-catalog mode.

## Settings keys (table `settings`)
bot keys (moderate_all, lead_target=`group`, group_chat_id, review_batch, search_mode, final_llm) +
`sla_operator_take_min`, `sla_agent_slots_min`, `sla_slots_to_client_min`, `client_promise_min` (override env),
`dashboard_metrics` (JSON list). Imported Google Sheets settings are stored as `sheets.<key>`.
