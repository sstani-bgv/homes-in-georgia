"""«Запрос оператору» (stage 6). After verification an agent's change of a locked field (properties.LOCKED_AFTER_VERIFY),
archiving or an owner transfer is not applied but queued here; the operator sees «было → стало» in «Запросы» and
approves (the change goes through the usual service functions, logged as the operator) or rejects with a comment.
payload: {field: {"old": …, "new": …}} plus plain ids the kind needs (owner_id for transfer_owner)."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeRequest, Owner, Property, User, at_least, is_field
from app.services import properties as P
from app.services.common import get_or_404, log_event, now
from app.services.errors import DomainError, forbidden

log = logging.getLogger(__name__)
KINDS = ("edit", "archive", "transfer_owner", "delete")  # delete is applied as an archive: objects are never deleted


def create(db: Session, entity: str, entity_id: int, kind: str, payload: dict, actor: User,
           reason: str | None = None) -> ChangeRequest:
    """No commit; the caller commits, then notify.change_request_new. A newer request of the same kind for the same
    object replaces the pending one (the operator decides on the latest wish only)."""
    if kind not in KINDS:
        raise DomainError("bad_kind", f"unknown request kind {kind}")
    for old in db.scalars(select(ChangeRequest).where(ChangeRequest.entity == entity, ChangeRequest.entity_id == entity_id,
                                                      ChangeRequest.kind == kind, ChangeRequest.status == "pending",
                                                      ChangeRequest.requested_by == actor.id)):
        old.status, old.decided_at, old.decision_comment = "rejected", now(), "заменён новым запросом"
    cr = ChangeRequest(entity=entity, entity_id=entity_id, kind=kind, payload=payload,
                       reason=(reason or "").strip() or None, requested_by=actor.id)
    db.add(cr)
    db.flush()
    log_event(db, entity, entity_id, "change_requested", actor, request_id=cr.id, kind=kind,
              fields=sorted(k for k, v in payload.items() if isinstance(v, dict)), comment=cr.reason)
    return cr


def request_transfer(db: Session, pid: int, to_agent_id: int, actor: User, reason: str | None = None) -> ChangeRequest | None:
    """An agent asks to move the object's owner to another agent (e.g. himself, or a colleague when he leaves).
    Staff transfer directly (properties.transfer_owner); then None is returned."""
    p = get_or_404(db, Property, int(pid), "property")
    o = db.get(Owner, p.owner_id) if p.owner_id else None
    if o is None or o.is_agent:
        raise DomainError("no_owner", "У объекта нет собственника")
    if at_least(actor, "operator"):
        P.transfer_owner(db, o.id, to_agent_id, actor, reason)
        return None
    if actor.id not in (p.agent_id, o.agent_id):
        raise forbidden("object is taken by another agent")
    to = get_or_404(db, User, int(to_agent_id), "user")
    if not is_field(to) or not to.is_active:
        raise DomainError("bad_agent", "Передать можно только активному агенту")
    if o.agent_id == to.id:
        raise DomainError("bad_status", "Собственник уже закреплён за этим агентом")
    old = db.get(User, o.agent_id) if o.agent_id else None
    cr = create(db, "property", p.id, "transfer_owner", {
        "owner_id": o.id, "agent_id": to.id,
        "owner_agent": {"old": old.name if old else None, "new": to.name}}, actor, reason)
    db.commit()
    P._notify("change_request_new", cr)
    return cr


def decide(db: Session, rid: int, operator: User, approve: bool, comment: str | None = None) -> ChangeRequest:
    if not at_least(operator, "operator"):
        raise forbidden("only operators decide requests")
    cr = get_or_404(db, ChangeRequest, int(rid), "request", lock=True)
    if cr.status != "pending":
        raise DomainError("bad_status", "Запрос уже рассмотрен")
    comment = (comment or "").strip() or None
    if not approve and not comment:
        raise DomainError("comment_required", "Напишите агенту, почему отказ")
    p = get_or_404(db, Property, cr.entity_id, "property", lock=True)
    if approve:
        _apply(db, cr, p, operator)
    cr.status, cr.decided_by, cr.decided_at, cr.decision_comment = "approved" if approve else "rejected", operator.id, now(), comment
    log_event(db, cr.entity, cr.entity_id, f"change_request_{cr.status}", operator, request_id=cr.id, kind=cr.kind,
              comment=comment)
    db.commit()
    P._notify("change_request_decided", cr)
    return cr


def _apply(db: Session, cr: ChangeRequest, p: Property, operator: User) -> None:
    """The approved change through the regular service functions (no commit)."""
    pl = cr.payload or {}
    if cr.kind == "edit":
        data = {k: v["new"] for k, v in pl.items() if isinstance(v, dict) and k not in P.POST_LOCKED}
        if any(k in pl for k in P.POST_LOCKED):
            pf = dict(p.post_fields or {})
            for k in P.POST_LOCKED:
                if k in pl:
                    pf[k] = pl[k]["new"]
                    if pf[k] is None:
                        del pf[k]
            data["post_fields"] = pf
        P.apply_update(db, p, data, operator)
    elif cr.kind in ("archive", "delete"):
        if p.status == "archived":
            raise DomainError("bad_status", "Объект уже в архиве")
        new = lambda k: (pl.get(k) or {}).get("new")  # noqa: E731
        a = P.check_archive(db, p, new("archive_reason"), new("archive_comment"), new("duplicate_of"))
        P._archive_now(db, p, operator, a["reason"], a["comment"], a["duplicate_of"])
    elif cr.kind == "transfer_owner":
        P.transfer_owner(db, pl["owner_id"], pl["agent_id"], operator, cr.reason, commit=False)


def pending_for(db: Session, p: Property) -> list[ChangeRequest]:
    return list(db.scalars(select(ChangeRequest).where(ChangeRequest.entity == "property", ChangeRequest.entity_id == p.id,
                                                       ChangeRequest.status == "pending").order_by(ChangeRequest.id)))


def outs(db: Session, rows: list[ChangeRequest]) -> list[dict]:
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(
        {i for r in rows for i in (r.requested_by, r.decided_by) if i})))} if rows else {}
    props = {x.id: x for x in db.scalars(select(Property).where(Property.id.in_({r.entity_id for r in rows})))} if rows else {}
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    name = lambda i: users[i].name if i in users else None  # noqa: E731
    return [{"id": r.id, "entity": r.entity, "entity_id": r.entity_id, "kind": r.kind, "status": r.status, "reason": r.reason,
             "changes": [{"field": k, "old": v.get("old"), "new": v.get("new")} for k, v in (r.payload or {}).items()
                         if isinstance(v, dict)],
             "requested_by": {"id": r.requested_by, "name": name(r.requested_by)},
             "decided_by": {"id": r.decided_by, "name": name(r.decided_by)} if r.decided_by else None,
             "decided_at": iso(r.decided_at), "decision_comment": r.decision_comment, "created_at": iso(r.created_at),
             "property": {"id": r.entity_id, "address": props[r.entity_id].address,
                          "status": props[r.entity_id].status} if r.entity_id in props else None} for r in rows]


def query(db: Session, user: User, status: str | None = "pending"):
    """The operators' queue «Запросы» (staff only: agents see their requests on the object card)."""
    stmt = select(ChangeRequest)
    if status:
        stmt = stmt.where(ChangeRequest.status.in_(status.split(",")))
    return stmt.order_by(ChangeRequest.id.desc() if status != "pending" else ChangeRequest.id)
