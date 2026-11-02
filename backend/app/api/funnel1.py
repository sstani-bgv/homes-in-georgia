"""Funnel 1: new listings, properties, owners."""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import STAFF_ROLES, current_user, paginate, require_roles
from app.db import get_db
from app.models import STAFF, Listing, Owner, Property, User
from app.services import properties as P
from app.services.common import get_or_404, log_event
from app.services.dedup import norm_address, norm_phone

router = APIRouter(tags=["objects"])


# ---------------------------------------------------------------- listings

@router.get("/listings/new")
def listings_new(q: str | None = None, source: str | None = None, flag: str | None = None,
                 limit: int = Query(50, ge=0, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.new_listings(db, q, source, flag, limit, offset)


@router.get("/listings/{listing_id}")
def listing_get(listing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    listing = get_or_404(db, Listing, listing_id, "listing")
    prop = db.scalar(select(Property).where(Property.listing_id == listing_id))
    # original links / text with phones / dup sources: staff and the agent holding the object only
    full = user.role in STAFF or (prop is not None and prop.agent_id == user.id and prop.status != "archived")
    d = P.listing_out(listing, full=full)
    d["property"] = {"id": prop.id, "status": prop.status, "agent_id": prop.agent_id} if prop else None
    return d


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
                    limit: int = 50, offset: int = 0, db: Session = Depends(get_db), user: User = Depends(current_user)):
    stmt = P.query(db, user, status, agent_id, q, bool(mine))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": P.outs(db, rows, user), "total": total}


@router.get("/properties/board")
def properties_board(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.board(db, user)


@router.post("/properties/check-duplicate")
def check_duplicate(body: DupIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    res = P.check_duplicate(db, body.phone, body.address, body.url, body.cadastral)
    if user.role not in STAFF:
        mine = set(db.scalars(select(Property.owner_id).where(Property.agent_id == user.id, Property.owner_id.isnot(None))))
        for r in res.get("results", []):
            if r.get("source") == "owners" and r.get("owner_id") not in mine:
                r["phone"] = r["owner"] = None
    return res


@router.post("/properties")
def properties_create(body: PropertyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = body.model_dump(exclude_none=True)
    p = P.take(db, data.pop("listing_id", None), user, data)
    return P.out(db, p, user, full=True)


@router.get("/properties/{pid}")
def properties_get(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.out(db, get_or_404(db, Property, pid, "property"), user, full=True)


@router.patch("/properties/{pid}")
def properties_patch(pid: int, body: PropertyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    data.pop("listing_id", None)
    return P.out(db, P.update(db, pid, data, user), user, full=True)


@router.post("/properties/{pid}/call")
def properties_call(pid: int, body: CallIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.out(db, P.call_result(db, pid, user, body.reached), user, full=True)


@router.post("/properties/{pid}/owner")
def properties_owner(pid: int, body: OwnerStepIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.out(db, P.owner_or_agent(db, pid, user, body.is_owner, body.owner_name, body.owner_phone), user, full=True)


@router.post("/properties/{pid}/shoot")
def properties_shoot(pid: int, body: ShootIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p, dup = P.schedule_shoot(db, pid, user, body.shoot_at)
    return {**P.out(db, p, user, full=True), "dup_check": dup}


@router.post("/properties/{pid}/media")
async def properties_media(pid: int, files: list[UploadFile] = File(...), room: str | None = Form(None),
                           db: Session = Depends(get_db), user: User = Depends(current_user)):
    payload = [(f.filename or "file", await f.read()) for f in files]
    P.upload_media(db, pid, payload, room, user)
    return P.out(db, get_or_404(db, Property, pid), user, full=True)


@router.delete("/properties/{pid}/media/{mid}")
def properties_media_delete(pid: int, mid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    P.delete_media(db, pid, mid, user)
    return {"ok": True}


@router.post("/properties/{pid}/checklist")
def properties_checklist(pid: int, body: dict, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.out(db, P.set_checklist(db, pid, body.get("checklist", body), user), user, full=True)


@router.post("/properties/{pid}/submit")
def properties_submit(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.out(db, P.submit_for_review(db, pid, user), user, full=True)


@router.post("/properties/{pid}/review")
def properties_review(pid: int, body: ReviewIn, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(*STAFF_ROLES))):
    return P.out(db, P.review(db, pid, user, body.approve, body.comment), user, full=True)


@router.post("/properties/{pid}/publish")
def properties_publish(pid: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    return P.out(db, P.publish(db, pid, user), user, full=True)


@router.post("/properties/{pid}/archive")
def properties_archive(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return P.out(db, P.archive(db, pid, user), user, full=True)


@router.get("/properties/{pid}/history")
def properties_history(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
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
            "is_agent": o.is_agent, "notes": o.notes, "created_by": o.created_by,
            "created_at": o.created_at.isoformat() if o.created_at else None}


@router.get("/owners")
def owners_list(q: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    stmt = select(Owner)
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
    return paginate(db, stmt.order_by(Owner.id.desc()), lambda o: owner_out(o, o.id in mine), limit, offset)


@router.post("/owners")
def owners_create(body: OwnerIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    o, _ = P.upsert_owner(db, body.name, body.phone, body.address, body.link, bool(body.is_agent), user)
    if body.notes:
        o.notes = body.notes
    db.commit()
    return owner_out(o)


@router.patch("/owners/{oid}")
def owners_patch(oid: int, body: OwnerIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
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
