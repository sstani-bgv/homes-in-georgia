"""Funnel 1: new listings, properties, owners."""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import STAFF_ROLES, current_user, paginate, require_roles
from app.db import get_db
from app.models import PUBLICATION_CHANNELS, STAFF, Lead, Listing, Owner, Property, User
from app.services.errors import forbidden
from app.services import change_requests as CR
from app.services import demand, matching
from app.services import properties as P
from app.services.leads import ACTIVE
from app.services.common import get_or_404, log_event
from app.services.dedup import norm_address, norm_phone

router = APIRouter(tags=["objects"])


# ---------------------------------------------------------------- listings

@router.get("/listings/new")
def listings_new(q: str | None = None, source: str | None = None, flag: str | None = None,
                 district: str | None = None, metro: str | None = None, bedrooms: str | None = None,
                 price_min: int | None = Query(None, ge=0), price_max: int | None = Query(None, ge=0),
                 limit: int = Query(50, ge=0, le=500), offset: int = Query(0, ge=0), known: int = 0,
                 db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.new_listings(db, q, source, flag, limit, offset, district, metro, price_min, price_max, bedrooms, bool(known))


@router.get("/listings/new/facets")
def listings_new_facets(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.new_listing_facets(db)


@router.get("/listings/{listing_id}")
def listing_get(listing_id: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    listing = get_or_404(db, Listing, listing_id, "listing")
    prop = db.scalar(select(Property).where(Property.listing_id == listing_id))
    # The assigned viewing agent also needs the source contact to arrange the viewing.
    assigned = db.scalar(select(Lead.id).where(Lead.listing_id == listing_id, Lead.agent_id == user.id,
                                              Lead.status.in_(ACTIVE)).limit(1))
    full = user.role in STAFF or assigned is not None or (prop is not None and prop.agent_id == user.id and prop.status != "archived")
    d = P.listing_out(listing, full=full)
    d["contacts_visible"] = full
    if full and prop and prop.owner_id:
        owner = db.get(Owner, prop.owner_id)
        if owner:
            d["owner"] = {"name": owner.name, "phone": owner.phone, "is_agent": owner.is_agent}
    d["property"] = {"id": prop.id, "status": prop.status, "agent_id": prop.agent_id} if prop else None
    d["similar"] = P.similar_in_base(db, [listing]).get(listing.id, [])
    d["refused"] = P.refusals_for(db, [listing.id]).get(listing.id)
    return d


class ListingAgentIn(BaseModel):
    owner_name: str | None = None
    owner_phone: str | None = None


@router.post("/listings/{listing_id}/dismiss")
def listing_dismiss(listing_id: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    P.dismiss_listing(db, listing_id, user)
    return {"ok": True}


@router.post("/listings/{listing_id}/agent")
def listing_agent(listing_id: str, body: ListingAgentIn, db: Session = Depends(get_db, scope="function"),
                  user: User = Depends(current_user)):
    return P.out(db, P.mark_listing_agent(db, listing_id, user, body.owner_name, body.owner_phone), user, full=True)


class MatchDecisionIn(BaseModel):
    same: bool


@router.post("/listings/{listing_id}/matches/{pid}")
def listing_match_decide(listing_id: str, pid: int, body: MatchDecisionIn, db: Session = Depends(get_db, scope="function"),
                         user: User = Depends(require_roles(*STAFF_ROLES))):
    """«Тот же объект» (same=true: the ad is linked to the object) / «Другой» on a «Похож на объект #N» card."""
    m = matching.decide(db, listing_id, pid, user, body.same)
    return {"ok": True, "status": m.status}


# ---------------------------------------------------------------- properties

class PropertyIn(BaseModel):
    listing_id: str | None = None
    address: str | None = None
    district: str | None = None
    cadastral_code: str | None = None
    price: float | None = None
    currency: str | None = None
    bedrooms: int | None = None
    rooms: int | None = None
    area: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    description: str | None = None
    is_exclusive: bool | None = None
    source_url: str | None = None
    source_type: str | None = None
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_id: int | None = None  # picked from the owners list
    post_fields: dict | None = None  # services.properties.POST_ONE / POST_MANY / POST_NUM
    reason: str | None = None  # why: goes with a «Запрос оператору» for locked fields (stage 6)


class CallIn(BaseModel):
    reached: bool


class OwnerStepIn(BaseModel):
    is_owner: bool
    owner_name: str | None = None
    owner_phone: str | None = None


class ShootIn(BaseModel):
    shoot_at: datetime


class ReviewIn(BaseModel):
    approve: bool
    comment: str | None = None


class DupIn(BaseModel):
    phone: str | None = None
    address: str | None = None
    url: str | None = None
    cadastral: str | None = None


@router.get("/properties")
def properties_list(status: str | None = None, agent_id: int | None = None, q: str | None = None, mine: int = 0,
                    base: int = 0, limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"),
                    user: User = Depends(current_user)):
    """base=1: «База объектов» — only objects shot and verified by the operator (P.BASE_STATUSES, published at least once)."""
    stmt = P.query(db, user, status, agent_id, q, bool(mine), base=bool(base))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": P.outs(db, rows, user), "total": total}


@router.get("/properties/counts")
def properties_counts(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """objects per status, for «В базе N объектов» (counts only, nothing personal)"""
    return P.status_counts(db)


@router.get("/properties/board")
def properties_board(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.board(db, user)


@router.get("/properties/deal-search")
def properties_deal_search(q: str | None = None, pid: int | None = None, deal_type: str = "rent", lead_id: int | None = None,
                           db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Stage 7: the deal form's object picker (agents see another agent's object as blocked, without its data)."""
    lead = db.get(Lead, lead_id) if lead_id else None
    allow = lead.property_id if lead is not None and (user.role in STAFF or lead.agent_id == user.id) else None
    return {"items": P.deal_search(db, user, q, pid, deal_type, allow)}


@router.post("/properties/check-duplicate")
def check_duplicate(body: DupIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    res = P.check_duplicate(db, body.phone, body.address, body.url, body.cadastral)
    if user.role not in STAFF:
        mine = set(db.scalars(select(Property.owner_id).where(Property.agent_id == user.id, Property.owner_id.isnot(None))))
        for r in res.get("results", []):
            if r.get("source") == "owners" and r.get("owner_id") not in mine:
                r["phone"] = r["owner"] = None
    return res


@router.post("/properties")
def properties_create(body: PropertyIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    data = body.model_dump(exclude_none=True)
    p = P.take(db, data.pop("listing_id", None), user, data)
    return P.out(db, p, user, full=True)


@router.get("/properties/{pid}")
def properties_get(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, get_or_404(db, Property, pid, "property"), user, full=True)


@router.patch("/properties/{pid}")
def properties_patch(pid: int, body: PropertyIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    data.pop("listing_id", None)
    p = P.update(db, pid, data, user, data.pop("reason", None))
    return _with_request(db, p, user)


def _with_request(db: Session, p: Property, user: User) -> dict:
    """The card, plus the «Запрос оператору» this call created (locked fields after verification), if any."""
    cr = getattr(p, "change_request", None)
    return {**P.out(db, p, user, full=True), "change_request": CR.outs(db, [cr])[0] if cr is not None else None}


@router.post("/properties/{pid}/call")
def properties_call(pid: int, body: CallIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.call_result(db, pid, user, body.reached), user, full=True)


@router.get("/properties/{pid}/demand")
def properties_demand(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Stage 10: active clients this object fits (agents: anonymized profiles, staff: who)."""
    return demand.for_property(db, get_or_404(db, Property, pid, "property"), user)


@router.post("/properties/{pid}/fast-track")
def properties_fast_track(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Собственник согласен показать клиентам»: client-eligible with the ad's photos, sent to the matched clients."""
    p, sent = demand.fast_track(db, pid, user)
    return {**P.out(db, p, user, full=True), "sent": len(sent)}


@router.post("/properties/{pid}/owner")
def properties_owner(pid: int, body: OwnerStepIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_or_agent(db, pid, user, body.is_owner, body.owner_name, body.owner_phone), user, full=True)


class RefusedIn(BaseModel):
    comment: str | None = None


@router.post("/properties/{pid}/refused")
def properties_refused(pid: int, body: RefusedIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_refused(db, pid, user, body.comment), user, full=True)


class ThinkingIn(BaseModel):
    until: datetime
    comment: str | None = None


@router.post("/properties/{pid}/thinking")
def properties_thinking(pid: int, body: ThinkingIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_thinking(db, pid, user, body.until, body.comment), user, full=True)


@router.post("/properties/{pid}/resume")
def properties_resume(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.resume(db, pid, user), user, full=True)


@router.post("/properties/{pid}/rented-self")
def properties_rented_self(pid: int, body: RefusedIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_rented_self(db, pid, user, body.comment), user, full=True)


@router.post("/properties/{pid}/shoot")
def properties_shoot(pid: int, body: ShootIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    p, dup = P.schedule_shoot(db, pid, user, body.shoot_at)
    return {**P.out(db, p, user, full=True), "dup_check": dup}


@router.post("/properties/{pid}/media")
async def properties_media(pid: int, files: list[UploadFile] = File(...), room: str | None = Form(None),
                           db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    payload = [(f.filename or "file", await f.read()) for f in files]
    P.upload_media(db, pid, payload, room, user)
    return P.out(db, get_or_404(db, Property, pid), user, full=True)


@router.patch("/properties/{pid}/media/{mid}")
def properties_media_room(pid: int, mid: int, body: dict, db: Session = Depends(get_db, scope="function"),
                          user: User = Depends(current_user)):
    m = P.set_media_room(db, pid, mid, body.get("room"), user)
    return {"id": m.id, "room": m.room}


@router.delete("/properties/{pid}/media/{mid}")
def properties_media_delete(pid: int, mid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    P.delete_media(db, pid, mid, user)
    return {"ok": True}


@router.post("/properties/{pid}/checklist")
def properties_checklist(pid: int, body: dict, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.set_checklist(db, pid, body.get("checklist", body), user), user, full=True)


@router.post("/properties/{pid}/submit")
def properties_submit(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.submit_for_review(db, pid, user), user, full=True)


@router.post("/properties/{pid}/review")
def properties_review(pid: int, body: ReviewIn, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(require_roles(*STAFF_ROLES))):
    return P.out(db, P.review(db, pid, user, body.approve, body.comment), user, full=True)


@router.post("/properties/{pid}/verify")
def properties_verify(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return P.out(db, P.verify(db, pid, user), user, full=True)


@router.get("/properties/{pid}/post")
def properties_post(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """The card's post generator: the channel caption built from the object, its hashtags, the channels to post to.
    Staff, and the object's agent (he fills the generator and sees the post before sending it to moderation)."""
    p = get_or_404(db, Property, pid, "property")
    if user.role not in STAFF and p.agent_id != user.id:
        raise forbidden("only the object's agent or staff")
    text = P.post_text(db, p)
    return {"text": text, "hashtags": P.hashtags(text), "channels": list(PUBLICATION_CHANNELS)}


class PublishIn(BaseModel):
    text: str | None = None
    media_ids: list[int] | None = None  # picked files in album order; None = all
    channel: str = "tg"
    comment: str | None = None


@router.post("/properties/{pid}/publish")
def properties_publish(pid: int, body: PublishIn | None = None, db: Session = Depends(get_db, scope="function"),
                       user: User = Depends(require_roles(*STAFF_ROLES))):
    b = body or PublishIn()
    return P.out(db, P.publish(db, pid, user, b.text, b.media_ids, b.channel, b.comment), user, full=True)


@router.get("/properties/{pid}/elsewhere")
def properties_elsewhere(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Where else the object is advertised (the exclusive toggle shows it). Links only for staff / the object's agent."""
    p = get_or_404(db, Property, pid, "property")
    show = P.can_see_owner(p, user)
    return {"items": [{**a, "url": a["url"] if show else None} for a in matching.elsewhere(db, p)]}


@router.post("/properties/{pid}/listings/{listing_id}/removed")
def properties_ad_removed(pid: int, listing_id: str, db: Session = Depends(get_db, scope="function"),
                          user: User = Depends(require_roles(*STAFF_ROLES))):
    return P.out(db, P.ad_removed(db, pid, listing_id, user), user, full=True)


class ArchiveIn(BaseModel):
    reason: str | None = None  # models.ARCHIVE_REASONS
    comment: str | None = None
    duplicate_of: int | None = None


@router.post("/properties/{pid}/archive")
def properties_archive(pid: int, body: ArchiveIn | None = None, db: Session = Depends(get_db, scope="function"),
                       user: User = Depends(current_user)):
    b = body or ArchiveIn()
    return _with_request(db, P.archive(db, pid, user, b.reason, b.comment, b.duplicate_of), user)


# ---------------------------------------------------------------- «Запросы» (stage 6)

class DecideIn(BaseModel):
    approve: bool
    comment: str | None = None


@router.get("/change-requests")
def change_requests_list(status: str | None = "pending", limit: int = 100, offset: int = 0,
                         db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    stmt = CR.query(db, user, status)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = list(db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))))
    return {"items": CR.outs(db, rows), "total": total}


@router.post("/change-requests/{rid}/decide")
def change_requests_decide(rid: int, body: DecideIn, db: Session = Depends(get_db, scope="function"),
                           user: User = Depends(require_roles(*STAFF_ROLES))):
    return CR.outs(db, [CR.decide(db, rid, user, body.approve, body.comment)])[0]


@router.get("/properties/{pid}/history")
def properties_history(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    get_or_404(db, Property, pid, "property")
    return P.history(db, pid)


# ---------------------------------------------------------------- owners

class OwnerIn(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None
    link: str | None = None
    is_agent: bool | None = None
    notes: str | None = None


def owner_out(o: Owner, show_phone: bool = True) -> dict:
    return {"id": o.id, "name": o.name, "phone": o.phone if show_phone else None, "address": o.address, "link": o.link,
            "is_agent": o.is_agent, "notes": o.notes, "created_by": o.created_by, "agent_id": o.agent_id,
            "created_at": o.created_at.isoformat() if o.created_at else None}


@router.get("/owners")
def owners_list(q: str | None = None, scope: str = "all", limit: int = 50, offset: int = 0,
                db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """scope: base = owners of objects the agent shot and sent (review / published / rented / verified); unlinked = the old
    Owner_Base contacts with no object yet (agents pick them in forms, the link appears then); agents; all."""
    stmt = select(Owner)
    in_base = select(Property.owner_id).where(Property.owner_id.isnot(None), or_(
        Property.status.in_(("review", "published", "rented")), Property.verified_at.isnot(None)))
    if scope == "base":
        stmt = stmt.where(Owner.id.in_(in_base), Owner.is_agent.is_(False))
    elif scope == "unlinked":
        stmt = stmt.where(~Owner.id.in_(select(Property.owner_id).where(Property.owner_id.isnot(None))), Owner.is_agent.is_(False))
    elif scope == "agents":
        stmt = stmt.where(Owner.is_agent.is_(True))
    if q:
        like = f"%{q.strip()}%"
        pn = norm_phone(q)
        conds = [Owner.name.ilike(like), Owner.address.ilike(like)]
        if user.role in STAFF and len(pn) > 3:
            conds.append(Owner.phone_norm.ilike(f"%{pn}%"))
        elif len(pn) >= 9:  # agents: full number only, no digit-by-digit search of hidden phones
            conds.append(Owner.phone_norm == pn)
        stmt = stmt.where(or_(*conds))
    if user.role in STAFF:
        return paginate(db, stmt.order_by(Owner.id.desc()), owner_out, limit, offset)
    mine = set(db.scalars(select(Property.owner_id).where(Property.agent_id == user.id, Property.owner_id.isnot(None))))
    return paginate(db, stmt.order_by(Owner.id.desc()), lambda o: owner_out(o, o.id in mine or o.agent_id == user.id),
                    limit, offset)


@router.post("/owners")
def owners_create(body: OwnerIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    o, _ = P.upsert_owner(db, body.name, body.phone, body.address, body.link, bool(body.is_agent), user)
    if body.notes:
        o.notes = body.notes
    db.commit()
    return owner_out(o)


@router.patch("/owners/{oid}")
def owners_patch(oid: int, body: OwnerIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    o = get_or_404(db, Owner, oid, "owner")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(o, k, v)
    if "phone" in data:
        o.phone_norm = norm_phone(o.phone)
    if "address" in data:
        o.address_norm = norm_address(o.address)
    log_event(db, "owner", o.id, "updated", user, fields=sorted(data))
    db.commit()
    return owner_out(o)


class TransferIn(BaseModel):
    agent_id: int
    reason: str | None = None


@router.post("/owners/{oid}/transfer")
def owners_transfer(oid: int, body: TransferIn, db: Session = Depends(get_db, scope="function"),
                    user: User = Depends(require_roles(*STAFF_ROLES))):
    """«Передать собственника агенту…» (stage 6.5): the owner and his live objects move to another agent."""
    return owner_out(P.transfer_owner(db, oid, body.agent_id, user, body.reason))


@router.post("/properties/{pid}/transfer-request")
def properties_transfer_request(pid: int, body: TransferIn, db: Session = Depends(get_db, scope="function"),
                                user: User = Depends(current_user)):
    """An agent's «Запрос оператору» to move the object's owner to another agent (staff: transferred right away)."""
    cr = CR.request_transfer(db, pid, body.agent_id, user, body.reason)
    return {"change_request": CR.outs(db, [cr])[0] if cr else None}
