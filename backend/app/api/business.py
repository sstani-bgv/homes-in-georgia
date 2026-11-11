"""Deals, evaluations, reports, counters, analytics."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import STAFF_ROLES, current_user, paginate, require_roles
from app.db import get_db
from app.models import STAFF, Deal, Evaluation, Property, User
from app.services import analytics, deals, evaluations, reports, users
from app.services import properties as P
from app.services.common import get_or_404
from app.services.errors import forbidden

router = APIRouter(tags=["deals"])


class DealIn(BaseModel):
    model_config = ConfigDict(extra="allow")  # legacy form fields (t0_h2o_client, recruit, ...) pass through
    lead_id: int | None = None
    property_id: int | None = None
    agent_id: int | None = None
    recruit_id: int | None = None
    deal_type: str = "rent"
    sub_type: str | None = None
    is_exclusive: bool = False
    is_rerented: bool = False
    price: float | None = None
    commission_pct: float | None = None
    commission_total: float | None = None
    platform_amount: float | None = None
    recruit_pct: float | None = None
    recruit_amount: float | None = None
    client_src: str | None = None
    agent_level: int | None = None
    cadastral: str | None = None
    ad_link: str | None = None
    address: str | None = None
    metro: str | None = None
    client_name: str | None = None
    client_phone: str | None = None
    client_tg: str | None = None
    client_birth: date | None = None
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_birth: date | None = None
    rent_from: date | None = None
    rent_to: date | None = None
    t0: dict | None = None
    scenario: dict | None = None  # Molecula axis pick {axis, opt, role, lev, n}
    split: list[dict] | None = None  # staff's manual distribution rows [{who, role, pct}]
    new_object: bool = False  # stage 7 «Объекта нет в базе»: create the object from the form's fields


class ReasonIn(BaseModel):
    reason: str | None = None


class EvaluationIn(BaseModel):
    deal_id: int
    kind: str = "t14"
    author: str = "agent"
    h2o_client: int | None = None
    h2o_owner: int | None = None
    h2o_process: int | None = None
    result: str | None = None
    changes: str | None = None
    complaints: bool = False
    complaints_text: str | None = None
    comment: str | None = None


@router.post("/deals/calc")
def deals_calc(body: DealIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    data = body.model_dump()
    if user.role not in STAFF:  # the same fixed commission deals.create applies to an agent's deal
        data["commission_total"] = None
        data["split"] = None
        data["commission_pct"] = deals.default_commission_pct(data.get("deal_type"), data.get("sub_type"))
    if not data.get("agent_level"):
        agent = db.get(User, data["agent_id"]) if data.get("agent_id") else user
        data["agent_level"] = users.effective_level(agent)
    return deals.calc(**data)


@router.get("/deals")
def deals_list(month: str | None = None, agent_id: int | None = None, status: str | None = None, limit: int = 50,
               offset: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return paginate(db, deals.query(db, user, month, agent_id, status), lambda d: deals.out(d, user), limit, offset)


@router.get("/deals/expiring")
def deals_expiring(days: int = 30, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return [deals.out(d, user) for d in deals.expiring(db, user, days)]


@router.get("/deals/find")
def deals_find(cadastral: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    rows = deals.find(db, cadastral)
    if user.role in STAFF:
        return [deals.out(d, user) for d in rows]
    # agents only learn that the object was rented and when; no amounts, names or contacts
    return [{"id": d.id, "cadastral": d.cadastral, "address": d.address, "status": d.status,
             "rent_from": d.rent_from.isoformat() if d.rent_from else None,
             "rent_to": d.rent_to.isoformat() if d.rent_to else None} for d in rows]


@router.post("/deals")
def deals_create(body: DealIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    d = deals.create(db, body.model_dump(exclude_none=True), user)
    return deals.out(d, user)


@router.get("/deals/{deal_id}")
def deals_get(deal_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    d = get_or_404(db, Deal, deal_id, "deal")
    if user.role not in STAFF and user.id not in (d.agent_id, d.recruit_id):
        raise forbidden("not your deal")
    out = deals.out(d, user)
    out["evaluations"] = [evaluations.out(e) for e in db.scalars(evaluations.query(db, deal_id))]
    out["rented_text"] = deals.rented_text(d, db)
    p = db.get(Property, d.property_id) if d.property_id else None
    out["property"] = {"id": p.id, "address": p.address, "status": p.status, "needs_check": P.needs_check(p)} if p else None
    return out


@router.post("/deals/{deal_id}/confirm")
def deals_confirm(deal_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return deals.out(deals.confirm(db, deal_id, user), user)


@router.post("/deals/{deal_id}/cancel")
def deals_cancel(deal_id: int, body: ReasonIn, db: Session = Depends(get_db, scope="function"),
                 user: User = Depends(require_roles(*STAFF_ROLES))):
    return deals.out(deals.cancel(db, deal_id, user, body.reason), user)


@router.post("/evaluations")
def evaluations_create(body: EvaluationIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return evaluations.out(evaluations.create(db, body.model_dump(), user))


@router.get("/evaluations")
def evaluations_list(deal_id: int | None = None, kind: str | None = None, limit: int = 50, offset: int = 0,
                     db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    stmt = evaluations.query(db, deal_id, kind)
    if user.role not in STAFF_ROLES:
        stmt = stmt.where(Evaluation.deal_id.in_(
            select(Deal.id).where(or_(Deal.agent_id == user.id, Deal.recruit_id == user.id))))
    return paginate(db, stmt, evaluations.out, limit, offset)


@router.post("/reports/{kind}")
def reports_create(kind: str, body: dict, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return reports.out(reports.save(db, kind, body, user))


@router.get("/reports")
def reports_list(kind: str | None = None, author_id: int | None = None, month: str | None = None, limit: int = 50,
                 offset: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return paginate(db, reports.query(db, user, kind, author_id, month), reports.out, limit, offset)


@router.get("/counters")
def counters(month: str | None = None, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return reports.counters(db, month, user)


@router.get("/analytics/dashboard")
def analytics_dashboard(month: str | None = None, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return {**analytics.dashboard(db, month), "stats": analytics.stats(db, user)}


@router.get("/analytics/funnel")
def analytics_funnel(date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"), db: Session = Depends(get_db, scope="function"),
                     user: User = Depends(require_roles(*STAFF_ROLES))):
    return analytics.funnel(db, date_from, date_to)


@router.get("/analytics/listings")
def analytics_listings(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return {**analytics.listings_stats(db), "weekly": analytics.weekly_listings(db)}


@router.get("/analytics/agents")
def analytics_agents(month: str | None = None, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return analytics.agents(db, month)
