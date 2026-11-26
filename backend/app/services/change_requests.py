"""«Запрос оператору» (stage 6). After verification an agent's change of a locked field (properties.LOCKED_AFTER_VERIFY),
archiving or an owner transfer is not applied but queued here; the operator sees «было → стало» in «Запросы» and
approves (the change goes through the usual service functions, logged as the operator) or rejects with a comment.
payload: {field: {"old": …, "new": …}} plus plain ids the kind needs (owner_id for transfer_owner)."""
import logging
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeRequest, Owner, Property, PropertyMedia, User, at_least, is_field
from app.services import properties as P
from app.services.common import get_or_404, log_event, notify_later, now
from app.services.errors import DomainError, forbidden

log = logging.getLogger(__name__)
# delete is applied as an archive: objects are never deleted; no_esign: the exclusive contract step without the owner's
# e-signature (Irakli 11-18), payload {"media_id": the scan / photo of a paper contract or None}
KINDS = ("edit", "archive", "transfer_owner", "delete", "no_esign")


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
    if o is None and at_least(actor, "operator"):  # David 11-26 #15102: a channel import has no owner — staff give it an agent
        to = get_or_404(db, User, int(to_agent_id), "user")
        if not is_field(to) or not to.is_active:
            raise DomainError("bad_agent", "Передать можно только активному агенту")
        old = db.get(User, p.agent_id) if p.agent_id else None
        p.agent_id, p.updated_at = to.id, now()
        log_event(db, "property", p.id, "agent_assigned", actor, from_agent_id=old.id if old else None, to_agent_id=to.id,
                  reason=(reason or "").strip() or None, comment=f"{old.name if old else P.PLATFORM} → {to.name}")
        db.commit()
        return None
    if o is None or o.is_agent:
        raise DomainError("no_owner", "У объекта нет собственника")
    if at_least(actor, "operator"):
        P.transfer_owner(db, o.id, to_agent_id, actor, reason, with_property=p.id)
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
        "owner_agent": {"old": old.name if old else P.PLATFORM, "new": to.name}}, actor, reason)
    db.commit()
    P._notify("change_request_new", cr)
    P._notify("prop_event", p, f"✋ <b>{escape(actor.name or '')} просит передать вам собственника {escape(o.name or '—')}</b>",
              actor.id, [to.id], False, "Решает оператор в «Запросах».")
    return cr


def request_owner(db: Session, pid: int | None, phone: str, actor: User, reason: str | None = None) -> ChangeRequest:
    """David 11-24 (voices 287409 / 287423, Luka and Zaza): the number the agent typed is an owner bound to another agent
    («Собственник закреплён за #Tag») — the agent asks the operator to move the owner to him and link him to this
    object; the owner's agent hears of it (he may say he is against it). Not when that agent has an exclusive with him.
    pid None: from «Проверка по телефону» (David 11-24 17:05) — an owner-level request (entity owner), no object to link."""
    p = get_or_404(db, Property, int(pid), "property") if pid is not None else None
    if p is not None and p.agent_id != actor.id:
        raise forbidden("only the object's agent")
    o = P.find_owner(db, phone)
    if o is None or o.is_agent or o.agent_id in (None, actor.id):
        raise DomainError("bad_status", "Этот собственник ни за кем не закреплён — просто сохраните номер")
    if db.scalar(select(Property.id).where(Property.owner_id == o.id, Property.agent_id == o.agent_id,
                                           Property.is_exclusive.is_(True), Property.status.not_in(("archived", "rented"))).limit(1)):
        raise DomainError("exclusive", f"У {users_svc_tag(db, o.agent_id)} эксклюзив с этим собственником — передать нельзя")
    pending = db.scalars(select(ChangeRequest).where(ChangeRequest.kind == "transfer_owner", ChangeRequest.status == "pending"))
    if any((r.payload or {}).get("owner_id") == o.id or (p is not None and r.entity == "property" and r.entity_id == p.id)
           for r in pending):
        raise DomainError("bad_status", "Запрос уже отправлен, ждёт оператора")
    old = db.get(User, o.agent_id)
    payload = {"owner_id": o.id, "agent_id": actor.id,
               "owner_agent": {"old": old.name if old else P.PLATFORM, "new": actor.name}}
    if p is not None:
        payload["link"] = True
    cr = create(db, "property" if p is not None else "owner", p.id if p is not None else o.id, "transfer_owner",
                payload, actor, reason)
    db.commit()
    P._notify("change_request_new", cr)
    head = f"✋ <b>{escape(actor.name or '')} просит передать ему вашего собственника {escape(o.name or '—')}</b>"
    note = "Если вы против — напишите оператору. Решает оператор в «Запросах»."
    if p is not None:
        P._notify("prop_event", p, head, actor.id, [o.agent_id], False, note)
    else:
        P._notify("staff_event", f"{head}\n\n{escape(note)}", None, None, [o.agent_id], actor.id, False)
    return cr


def users_svc_tag(db: Session, uid: int | None) -> str:
    from app.services import users as users_svc
    return users_svc.tag_of(db.get(User, uid)) if uid else "—"


def request_no_esign(db: Session, pid: int, reason: str | None, actor: User,
                     scan: tuple[str, bytes] | None = None) -> ChangeRequest:
    """«Без электронной подписи»: the owner can't sign by the link (abroad, no smartphone, signed on paper) → a request
    to the operator with the reason and optionally a scan / photo of the paper contract. Approved, it stands for the
    exclusive contract step (properties.contract_done); rejected, the step is back with the operator's comment."""
    p = get_or_404(db, Property, int(pid), "property", lock=True)
    P._require_owner(p, actor)
    if not p.is_exclusive:
        raise DomainError("bad_status", "Объект не эксклюзив")
    if P.contract(db, p.id):
        raise DomainError("already_signed", "Договор уже подписан")
    w = P.no_esign(db, p.id)
    if w is not None and w.status == "approved":
        raise DomainError("bad_status", "Оператор уже подтвердил договор без электронной подписи")
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("reason_required", "Укажите, почему собственник не может подписать по ссылке")
    m = P._store_owner_doc(db, p, P.PAPER_CONTRACT, *scan, actor.id) if scan and scan[1] else None
    if m is not None:
        db.flush()
    cr = create(db, "property", p.id, "no_esign", {"media_id": m.id if m else None}, actor, reason[:500])
    P.schedule_dup_scan(db, p, "no_esign")  # §12 (David 11-19): no duplicates → approved by itself (similar.no_esign_gate)
    db.commit()
    P._notify("change_request_new", cr)
    return cr


def decide(db: Session, rid: int, operator: User, approve: bool, comment: str | None = None) -> ChangeRequest:
    if not at_least(operator, "operator"):
        raise forbidden("only operators decide requests")
    cr = get_or_404(db, ChangeRequest, int(rid), "request", lock=True)
    if cr.status != "pending":
        raise DomainError("bad_status", "Запрос уже рассмотрен")
    if cr.requested_by == operator.id:
        raise forbidden("Свой запрос решает другой оператор")
    comment = (comment or "").strip() or None
    if not approve and not comment:
        raise DomainError("comment_required", "Напишите агенту, почему отказ")
    p = get_or_404(db, Property, cr.entity_id, "property", lock=True) if cr.entity == "property" else None
    if approve:
        _apply(db, cr, p, operator)
    cr.status, cr.decided_by, cr.decided_at, cr.decision_comment = "approved" if approve else "rejected", operator.id, now(), comment
    log_event(db, cr.entity, cr.entity_id, f"change_request_{cr.status}", operator, request_id=cr.id, kind=cr.kind,
              comment=comment)
    db.commit()
    P._notify("change_request_decided", cr)
    return cr


def _apply(db: Session, cr: ChangeRequest, p: Property | None, operator: User) -> None:
    """The approved change through the regular service functions (no commit)."""
    pl = cr.payload or {}
    if cr.kind == "edit":
        sub = {k: k[3:] if k.startswith("pf:") else k for k in pl if k in P.POST_LOCKED or k.startswith("pf:")}
        data = {k: v["new"] for k, v in pl.items() if isinstance(v, dict) and k not in sub}
        if sub:  # post_fields key by key (properties.split_post / split_locked)
            pf = dict(p.post_fields or {})
            for k, name in sub.items():
                pf[name] = pl[k]["new"]
                if pf[name] is None:
                    del pf[name]
            data["post_fields"] = pf
        P.apply_update(db, p, data, operator)
    elif cr.kind in ("archive", "delete"):
        if p.status == "archived":
            raise DomainError("bad_status", "Объект уже в архиве")
        new = lambda k: (pl.get(k) or {}).get("new")  # noqa: E731
        a = P.check_archive(db, p, new("archive_reason"), new("archive_comment"), new("duplicate_of"))
        P._archive_now(db, p, operator, a["reason"], a["comment"], a["duplicate_of"])
        if a["reason"] == "rented_by_owner" and new("rent_to"):
            P.set_owner_lease(db, p, new("rent_from"), new("rent_to"), operator)
    elif cr.kind == "transfer_owner":
        P.transfer_owner(db, pl["owner_id"], pl["agent_id"], operator, cr.reason, commit=False)
        if pl.get("link") and p is not None and p.owner_id != pl["owner_id"]:  # request_owner: the owner goes onto the asking agent's object
            p.owner_id, p.updated_at = pl["owner_id"], now()
            log_event(db, "property", p.id, "owner_linked", operator, owner_id=pl["owner_id"], request_id=cr.id)
    elif cr.kind == "no_esign":  # the step counts as done from now (properties.contract_done reads the request)
        log_event(db, "property", p.id, "exclusive_contract_waived", operator, request_id=cr.id, reason=cr.reason,
                  requested_by=cr.requested_by, media_id=pl.get("media_id"))
        P.schedule_dup_scan(db, p, "contract")


def pending_for(db: Session, p: Property) -> list[ChangeRequest]:
    return list(db.scalars(select(ChangeRequest).where(ChangeRequest.entity == "property", ChangeRequest.entity_id == p.id,
                                                       ChangeRequest.status == "pending").order_by(ChangeRequest.id)))


def outs(db: Session, rows: list[ChangeRequest]) -> list[dict]:
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(
        {i for r in rows for i in (r.requested_by, r.decided_by) if i})))} if rows else {}
    props = {x.id: x for x in db.scalars(select(Property).where(Property.id.in_(
        {r.entity_id for r in rows if r.entity == "property"})))} if rows else {}
    owners = {x.id: x for x in db.scalars(select(Owner).where(Owner.id.in_(
        {r.entity_id for r in rows if r.entity == "owner"})))} if rows else {}
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    name = lambda i: users[i].name if i in users else None  # noqa: E731
    scans = {m.id: m for m in db.scalars(select(PropertyMedia).where(PropertyMedia.id.in_(
        {(r.payload or {}).get("media_id") for r in rows if r.kind == "no_esign"} - {None})))} if rows else {}
    scan = lambda r: f"/api/media/{scans[mid].path}" if (mid := (r.payload or {}).get("media_id")) in scans else None  # noqa: E731
    return [{"id": r.id, "entity": r.entity, "entity_id": r.entity_id, "kind": r.kind, "status": r.status, "reason": r.reason,
             "scan_url": scan(r), "dups": (r.payload or {}).get("dups") if r.kind == "no_esign" else None,
             "changes": [{"field": k, "old": v.get("old"), "new": v.get("new")} for k, v in (r.payload or {}).items()
                         if isinstance(v, dict)],
             "requested_by": {"id": r.requested_by, "name": name(r.requested_by)},
             "decided_by": {"id": r.decided_by, "name": name(r.decided_by)} if r.decided_by else None,
             "decided_at": iso(r.decided_at), "decision_comment": r.decision_comment, "created_at": iso(r.created_at),
             "property": {"id": r.entity_id, "address": props[r.entity_id].address,
                          "status": props[r.entity_id].status} if r.entity == "property" and r.entity_id in props else None,
             "owner": {"id": r.entity_id, "name": owners[r.entity_id].name}
             if r.entity == "owner" and r.entity_id in owners else None} for r in rows]


def query(db: Session, user: User, status: str | None = "pending"):
    """The operators' queue «Запросы» (staff only: agents see their requests on the object card)."""
    stmt = select(ChangeRequest)
    if status:
        stmt = stmt.where(ChangeRequest.status.in_(status.split(",")))
    return stmt.order_by(ChangeRequest.id.desc() if status != "pending" else ChangeRequest.id)
