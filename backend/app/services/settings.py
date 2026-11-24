"""Key/value settings (bot `settings` table). SLA minutes: DB setting wins over env."""
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import Setting

DEFAULTS = {
    "moderate_all": "0", "lead_target": "group", "group_chat_id": "", "review_batch": "5", "search_mode": "llm",
    "final_llm": "on", "auto_push_variants": "0",  # 0: new variants only by «Ещё» (a client's message refines the request)
    "sla_operator_take_min": str(env.SLA_OPERATOR_TAKE_MIN), "sla_agent_slots_min": str(env.SLA_AGENT_SLOTS_MIN),
    "sla_slots_to_client_min": str(env.SLA_SLOTS_TO_CLIENT_MIN), "client_promise_min": str(env.CLIENT_PROMISE_MIN),
    "sla_enabled": "1",  # 0: no step deadlines on leads (no timers, reminders, escalations)
    "take_idle_min": "1440",  # an object taken from «Новые объявления» and not in work goes back after N min (a day, David 11-22); 0 = off
    "owner_lease_remind_days": "30,7",  # «Собственник сдал»: Telegram to the agent N days before the lease ends
    "room_picker": "1",  # media upload: the per-photo room tag (always optional); 0 hides it
    # the room tags offered under a photo, one per line (Управление → Настройки, David 11-23); «Ванная» is now «Санузел»
    "photo_rooms": "\n".join(("Гостиная", "Кухня", "Спальня 1", "Спальня 2", "Спальня 3", "Санузел", "Балкон", "Терраса",
                              "Вид из окна", "Прихожая", "Гардероб", "Кабинет", "Подъезд", "Фасад")),
    # the Telegram channel of a post per kind (notify.post_chat, David / Stanislav 11-23): the community's channels; empty =
    # PUBLISH_CHAT_ID (cars, daily rent, developments: channels not known yet; daily / developments are not posted yet)
    "publish_chat_rent": "", "publish_chat_sale": "@sale_in_tbilisi", "publish_chat_commercial": "@homesingeorgia",
    "publish_chat_cars": "", "publish_chat_daily": "", "publish_chat_developments": "",
    "originals_chat_id": "",  # photos without the watermark after a publication (Irakli 11-23); the bot's /originals_here
    "dashboard_metrics": '["deals","platform","active_objects","leaderboard","funnel","response_times"]',
}


# userbot_session: the operator's Telegram login (a full account access), never leaves the server
HIDDEN = ("client_catalog", "userbot_tg_id", "userbot_session", "source_runs", "source_every_min", "llm_usage")
PROTECTED = ("group_chat_id", "lead_target", "publish_chat_rent", "publish_chat_sale", "publish_chat_commercial",
             "publish_chat_cars", "publish_chat_daily", "publish_chat_developments", "originals_chat_id")  # Telegram routing (an integration): superadmin only (Irakli 11-12)
NUMERIC_MINUTES = ("sla_operator_take_min", "sla_agent_slots_min", "sla_slots_to_client_min", "client_promise_min")
_MISS = object()


def _cache(db: Session) -> dict:
    """Read cache for the current transaction (a request's read path, e.g. a board of SLA colours); cleared on
    write/commit/rollback; missing keys (None) are cached too."""
    return db.info.setdefault("settings_cache", {})


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _drop_cache(session: Session):  # a cached value lives at most one transaction
    session.info.pop("settings_cache", None)


def get(db: Session, key: str, default: str | None = None) -> str | None:
    cache = _cache(db)
    value = cache.get(key, _MISS)
    if value is _MISS:
        row = db.get(Setting, key)
        value = cache[key] = row.value if row else None
    return value if value is not None else DEFAULTS.get(key, default)


def validate(body: dict) -> None:
    """Numeric SLA/promise keys must be whole minutes in 1..1440."""
    from app.services.errors import DomainError
    if "client_catalog" in body:
        raise DomainError("bad_setting", "Client catalog is limited to published agency objects and cannot be switched")
    for k in NUMERIC_MINUTES:
        if k not in body:
            continue
        v = body[k]
        try:
            n = float(str(v).strip())
        except (TypeError, ValueError):
            n = None
        if isinstance(v, bool) or n is None or not 1 <= n <= 1440 or not n.is_integer():
            raise DomainError("bad_setting", f"{k} must be a whole number of minutes in 1..1440")


    if "owner_lease_remind_days" in body:
        v = str(body["owner_lease_remind_days"]).replace(" ", "")
        if v and any(not x.isdecimal() or int(x) > 365 for x in v.split(",")):
            raise DomainError("bad_setting", "owner_lease_remind_days: days before the lease end, e.g. 30,7")
    if "take_idle_min" in body:
        v = str(body["take_idle_min"]).strip()
        if not v.isdigit() or int(v) > 1440:
            raise DomainError("bad_setting", "take_idle_min must be a whole number of minutes in 0..1440")


def set(db: Session, key: str, value, commit: bool = True) -> None:  # noqa: A001
    db.execute(insert(Setting).values(key=key, value=str(value))
               .on_conflict_do_update(index_elements=["key"], set_={"value": str(value)}))
    _cache(db).pop(key, None)
    if commit:
        db.commit()


def all(db: Session) -> dict:  # noqa: A001
    out = dict(DEFAULTS)
    # userbot_tg_id: written by the operator's userbot itself; source_runs: by the parser; source_every_min: its own
    # page (/sources/schedule). Not admin settings here: the settings form saves everything it got back.
    out.update({r.key: r.value for r in db.query(Setting).all() if r.key not in HIDDEN and not r.key.startswith("key:")})  # key:*: app/keys.py, never shown
    return out


def sla_minutes(db: Session, stage: str) -> int:
    try:
        return int(float(get(db, f"sla_{stage}_min") or 0)) or 5
    except ValueError:
        return 5


def client_promise_min(db: Session) -> int:
    try:
        return int(get(db, "client_promise_min") or env.CLIENT_PROMISE_MIN)
    except ValueError:
        return env.CLIENT_PROMISE_MIN
