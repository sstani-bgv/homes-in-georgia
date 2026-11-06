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
    "dashboard_metrics": '["deals","platform","active_objects","leaderboard","funnel","response_times"]',
}


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
        if isinstance(v, bool) or n is None or n != int(n) or not 1 <= n <= 1440:
            raise DomainError("bad_setting", f"{k} must be a whole number of minutes in 1..1440")


def set(db: Session, key: str, value, commit: bool = True) -> None:  # noqa: A001
    db.execute(insert(Setting).values(key=key, value=str(value))
               .on_conflict_do_update(index_elements=["key"], set_={"value": str(value)}))
    _cache(db).pop(key, None)
    if commit:
        db.commit()


def all(db: Session) -> dict:  # noqa: A001
    out = dict(DEFAULTS)
    out.update({r.key: r.value for r in db.query(Setting).all() if r.key != "client_catalog"})
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
