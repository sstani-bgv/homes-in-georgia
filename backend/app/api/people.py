"""Users, cabinet, clients."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.auth import user_out
from app.api.deps import ADMIN_ROLES, STAFF_ROLES, current_user, paginate, require_roles
from app.db import get_db
from app.models import STAFF, ChannelPost, Client, Deal, Lead, Listing, Property, Reaction, Report, User
from app.services import clients as client_card
from app.services import deals, leads, properties, reports, users
from app.services.common import TZ, get_or_404, jload, now
from app.services.errors import forbidden

router = APIRouter(tags=["users"])


class UserIn(BaseModel):
    name: str
    role: str = "agent"
    tg_id: int | None = None
    username: str | None = None
    phone: str | None = None
    level: int | None = None
    mentor_id: int | None = None
    districts: list[str] = []
    is_active: bool = True
    login: str | None = None
    password: str | None = None
    photo_url: str | None = None
    birth_date: date | None = None
    plans: dict | None = None
    agent_tag: str | None = None


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    tg_id: int | None = None
    username: str | None = None
    phone: str | None = None
    level: int | None = None
    mentor_id: int | None = None
    districts: list[str] | None = None
    is_active: bool | None = None
    login: str | None = None
    password: str | None = None
    photo_url: str | None = None
    birth_date: date | None = None
    plans: dict | None = None
    exclusive_deals: int | None = None
    agent_tag: str | None = None


class PromoteIn(BaseModel):
    level: int = 50


@router.get("/users")
def list_users(role: str | None = None, active: bool | None = None, db: Session = Depends(get_db, scope="function"),
               user: User = Depends(current_user)):
    rows = users.list_users(db, role, active)
    if user.role not in STAFF:  # agents see the team (names/levels), not logins
        return {"items": [{"id": u.id, "name": u.name, "username": u.username, "role": u.role,
                           "level": users.effective_level(u)} for u in rows if u.is_active], "total": len(rows)}
    return {"items": [user_out(u) for u in rows], "total": len(rows)}


@router.get("/users/agent-tag-suggestion")
def agent_tag_suggestion(name: str, uid: int | None = None, db: Session = Depends(get_db, scope="function"),
                         user: User = Depends(require_roles(*ADMIN_ROLES))):
    return {"tag": users.suggest_agent_tag(db, name, uid)}


@router.post("/users")
def create_user(body: UserIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    data = body.model_dump(exclude_none=True)
    return user_out(users.create(db, data, user))


@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    return user_out(users.update(db, uid, body.model_dump(exclude_unset=True), user))


@router.post("/users/{uid}/promote")
def promote(uid: int, body: PromoteIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*ADMIN_ROLES))):
    return user_out(users.promote(db, uid, body.level, user))


@router.get("/users/{uid}/stats")
def user_stats(uid: int, month: str | None = None, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    if user.role not in STAFF and user.id != uid:
        raise forbidden()
    u = get_or_404(db, User, uid, "user")
    y, m = reports.month_bounds(month)
    mo = f"{m}/{y}"
    month_deals = list(db.scalars(select(Deal).where(Deal.agent_id == uid, Deal.month == mo, Deal.status != "cancelled")))
    return {"user": user_out(u), **users.level_info(u), "month": mo,
            "deals_month": len(month_deals), "agent_amount_month": sum(float(d.agent_amount or 0) for d in month_deals),
            "deals_total": db.scalar(select(func.count()).where(Deal.agent_id == uid, Deal.status != "cancelled")),
            "properties": dict(db.execute(select(Property.status, func.count()).where(Property.agent_id == uid)
                                          .group_by(Property.status)).all()),
            "leads": dict(db.execute(select(Lead.status, func.count()).where(Lead.agent_id == uid)
                                     .group_by(Lead.status)).all()),
            "counters": next((c for c in reports.counters(db, mo) if c["user"]["id"] == uid), None)}


@router.get("/me/cabinet")
def cabinet(user_id: int | None = None, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """The personal cabinet; staff open any agent's with ?user_id=, everyone else only their own."""
    if user_id and user_id != user.id and user.role not in STAFF:
        raise forbidden()
    me = get_or_404(db, User, user_id, "user") if user_id else user
    y, m = reports.month_bounds(None)
    mo = f"{m}/{y}"
    today = now().astimezone(TZ).date()
    my_deals = list(db.scalars(deals.query(db, user).where((Deal.agent_id == me.id) | (Deal.recruit_id == me.id))
                               .limit(50)))
    viewing = db.scalars(select(Lead).where(Lead.agent_id == me.id, Lead.status == "time_chosen",
                                            Lead.viewing_at >= now()).order_by(Lead.viewing_at).limit(20))
    my_leads = db.scalars(select(Lead).where(Lead.agent_id == me.id, Lead.status.not_in(("rented", "refused")))
                          .order_by(Lead.created_ts.desc()).limit(50))
    my_props = db.scalars(select(Property).where(Property.agent_id == me.id, Property.status.not_in(("archived",)))
                          .order_by(Property.updated_at.desc()).limit(50))
    expiring = db.scalars(select(Deal).where(Deal.agent_id == me.id, Deal.status == "confirmed", Deal.rent_to >= today,
                                             Deal.rent_to <= today + timedelta(days=30)).order_by(Deal.rent_to))
    # objects whose lease (hand-typed rent_to) ends soon or just ended: the agent re-rents them
    leases = db.scalars(select(Property).where(Property.agent_id == me.id, Property.rent_to >= today - timedelta(days=14),
                                               Property.rent_to <= today + timedelta(days=60)).order_by(Property.rent_to))
    month_deals = [d for d in my_deals if d.month == mo and d.status != "cancelled"]
    # apps-script getCabinetData: showings / photo reports / Make-Foto queue where I'm the recruit, mentor or agent
    my_reports = db.scalars(select(Report).where(
        Report.kind.in_(("showing", "photo", "makefoto", "assigned_showing")),
        or_(Report.author_id == me.id, Report.recruit_id == me.id, Report.mentor_id == me.id),
        Report.date >= today - timedelta(days=90)).order_by(Report.date.desc(), Report.id.desc()).limit(100))
    week_ago = now() - timedelta(days=7)
    listings = db.execute(select(func.count().filter(ChannelPost.status == "active"),
                                 func.count().filter(ChannelPost.date >= week_ago))
                          .where(ChannelPost.agent_id == me.id)).one()
    return {
        "user": user_out(me), "level": users.level_info(me), "month": mo,
        "counters": next((c for c in reports.counters(db, mo) if c["user"]["id"] == me.id), None),
        "month_summary": {"deals": len(month_deals), "agent_amount": sum(float(d.agent_amount or 0) for d in month_deals),
                          "commission": sum(float(d.commission_total or 0) for d in month_deals)},
        "monthly": _monthly(db, me.id, y, m),
        "deals": [deals.out(d, user) for d in my_deals],
        "properties": [properties.out(db, p, user) for p in my_props],
        "leads": [leads.card(db, x, user) for x in my_leads],
        "upcoming_viewings": [leads.card(db, x, user) for x in viewing],
        "expiring_contracts": [deals.out(d, user) for d in expiring],
        "expiring_leases": [{"id": p.id, "address": p.address, "district": p.district, "status": p.status,
                             "rent_to": p.rent_to.isoformat(), "tenant_name": p.tenant_name} for p in leases],
        "reports": [reports.out(r) for r in my_reports],
        "channel_listings": {"active": listings[0], "last_7_days": listings[1]},
    }


def _monthly(db: Session, uid: int, y: int, m: int, n: int = 12) -> list[dict]:
    """Last n months, newest first: deals (rentals among them) by Deal.month, the agent's money (his share as agent plus
    as recruit), the platform's share of his deals, objects he added (Property.created_at, Tbilisi time)."""
    keys = [f"{(m - 1 - i) % 12 + 1}/{y + (m - 1 - i) // 12}" for i in range(n)]
    rows = {k: {"month": k, "deals": 0, "rentals": 0, "earned": 0.0, "platform": 0.0, "commission": 0.0, "added": 0}
            for k in keys}
    for d in db.scalars(select(Deal).where((Deal.agent_id == uid) | (Deal.recruit_id == uid), Deal.month.in_(keys),
                                           Deal.status != "cancelled")):
        r = rows[d.month]
        if d.agent_id == uid:
            r["deals"] += 1
            r["rentals"] += d.deal_type == "rent"
            r["earned"] += float(d.agent_amount or 0)
            r["platform"] += float(d.platform_amount or 0)
            r["commission"] += float(d.commission_total or 0)
        if d.recruit_id == uid:
            r["earned"] += float(d.recruit_amount or 0)
    oy, om = y + (m - n) // 12, (m - n) % 12 + 1  # the first day of the oldest month
    for (ts,) in db.execute(select(Property.created_at).where(Property.agent_id == uid,
                                                               Property.created_at >= datetime(oy, om, 1, tzinfo=TZ))):
        t = ts.astimezone(TZ)
        if (k := f"{t.month}/{t.year}") in rows:
            rows[k]["added"] += 1
    return list(rows.values())


def client_out(c: Client, full: bool = True) -> dict:
    d = {"id": c.id, "channel": c.channel, "external_id": c.external_id, "username": c.username, "name": c.name,
         "phone": c.phone, "lang": c.lang, "profile": jload(c.profile, {}), "profile_done": bool(c.profile_done),
         "created_at": c.created_at, "updated_at": c.updated_at}
    if not full:
        d.pop("external_id")
    return d


@router.get("/clients")
def list_clients(q: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"),
                 user: User = Depends(require_roles(*STAFF_ROLES))):
    stmt = select(Client)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Client.name.ilike(like), Client.username.ilike(like), Client.phone.ilike(like),
                              Client.external_id.ilike(like)))
    page = paginate(db, stmt.order_by(Client.id.desc()), client_out, limit, offset)
    counts = dict(db.execute(select(Lead.client_id, func.count()).group_by(Lead.client_id)).all())
    for it in page["items"]:
        it["leads"] = counts.get(it["id"], 0)
    return page


@router.get("/clients/{cid}")
def get_client(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    c = get_or_404(db, Client, cid, "client")
    d = {**client_out(c), **client_card.card(db, c)}  # stage 8: status, timeline, refusals
    d["leads"] = [leads.card(db, x, user) for x in db.scalars(select(Lead).where(Lead.client_id == cid)
                                                            .order_by(Lead.created_ts.desc()))]
    d["reactions"] = [{"listing_id": r.listing_id, "reaction": r.reaction, "created_at": r.created_at,
                       "listing": leads._listing_card(db.get(Listing, r.listing_id))}
                      for r in db.scalars(select(Reaction).where(Reaction.client_id == cid).order_by(Reaction.created_at.desc()))]
    return d
