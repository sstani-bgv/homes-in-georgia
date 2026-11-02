"""T0 / T14 (H2O = client, owner, process; 0-3). Low score or complaints → operators (A6)."""
import html
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import STAFF, Deal, Evaluation, User
from app.services.common import get_or_404, log_event
from app.services.errors import DomainError, forbidden
from app.telegram import notify

log = logging.getLogger(__name__)


def _score(v):
    if v in (None, ""):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise DomainError("bad_score", "H2O scores are 0-3")
    if not 0 <= n <= 3:
        raise DomainError("bad_score", "H2O scores are 0-3")
    return n


def is_alarming(ev: Evaluation) -> bool:
    return ev.complaints or any(s is not None and s <= 1 for s in (ev.h2o_client, ev.h2o_owner, ev.h2o_process)) \
        or ev.result == "dirty"


def create(db: Session, data: dict, actor: User | None, commit: bool = True) -> Evaluation:
    kind = data.get("kind") or "t14"
    if kind not in ("t0", "t14"):
        raise DomainError("bad_kind", "kind must be t0 or t14")
    deal = get_or_404(db, Deal, int(data["deal_id"]), "deal") if data.get("deal_id") else None
    if actor is not None and actor.role not in STAFF and deal and actor.id not in (deal.agent_id, deal.recruit_id):
        raise forbidden("not your deal")
    complaints = data.get("complaints")
    complaints = complaints in (True, "yes", "true", "1", 1)
    ev = Evaluation(deal_id=deal.id if deal else None, kind=kind, author=data.get("author") or "agent",
                    h2o_client=_score(data.get("h2o_client")), h2o_owner=_score(data.get("h2o_owner")),
                    h2o_process=_score(data.get("h2o_process")),
                    result=data.get("result") or data.get("deal_result") or None, changes=data.get("changes"),
                    complaints=complaints, complaints_text=data.get("complaints_text"),
                    comment=data.get("comment") or data.get("agent_comment"),
                    raw={k: str(v) for k, v in data.items()})
    if ev.result not in (None, "clean", "dirty"):
        ev.result = None
    db.add(ev)
    db.flush()
    if deal and kind == "t14":
        deal.t14_status = "done"
    log_event(db, "deal" if deal else "evaluation", deal.id if deal else ev.id, f"evaluation_{kind}", actor,
              label=None if actor else ev.author, evaluation_id=ev.id, author=ev.author)
    if commit:
        db.commit()
        notify_if_alarming(ev)
    return ev


def notify_if_alarming(ev: Evaluation):
    if is_alarming(ev):
        try:
            notify.evaluation_alert(ev)
        except Exception:  # noqa: BLE001
            log.exception("notify.evaluation_alert failed")


def t14_text(db: Session, ev: Evaluation) -> str:
    """apps-script saveT14 topic text."""
    deal = db.get(Deal, ev.deal_id) if ev.deal_id else None
    agent = db.get(User, deal.agent_id) if deal and deal.agent_id else None
    e = lambda v: html.escape(str(v)) if v not in (None, "") else "—"  # noqa: E731
    return (f"🔍 <b>T14 — Проверка сделки</b>\n\nОбъект: {e(deal.ad_link or deal.address if deal else None)}\n"
            f"Агент: {e(agent.name if agent else None)}\nДата: {e(deal.rent_from if deal else None)}\n\n"
            f"<b>H2O:</b> {ev.h2o_client}/{ev.h2o_owner}/{ev.h2o_process}\n\nИзменения: {e(ev.changes)}\n"
            f"Жалобы: {'⚠️ ' + e(ev.complaints_text) if ev.complaints else 'нет'}\n\n"
            f"{'✅' if ev.result == 'clean' else '🔴'} <b>{'Чистая' if ev.result == 'clean' else 'Грязная'}</b>\n\n💬 {e(ev.comment)}")


def out(ev: Evaluation) -> dict:
    return {c: (v.isoformat() if hasattr(v, "isoformat") else v)
            for c in Evaluation.__table__.columns.keys() for v in [getattr(ev, c)]}


def query(db: Session, deal_id: int | None = None, kind: str | None = None):
    stmt = select(Evaluation)
    if deal_id:
        stmt = stmt.where(Evaluation.deal_id == deal_id)
    if kind:
        stmt = stmt.where(Evaluation.kind == kind)
    return stmt.order_by(Evaluation.created_at.desc())
