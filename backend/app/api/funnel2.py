"""Funnel 2: leads."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import STAFF_ROLES, current_user, require_roles
from app.db import get_db
from app.models import Client, Deal, Lead, Listing, Property, User
from app.services import catalog, contracts
from app.services import deals as deals_svc
from app.services import leads as L
from app.services.common import get_or_404
from app.services.errors import forbidden

router = APIRouter(tags=["leads"])

# stages before the agent: an agent neither sees them on the board nor moves a lead there (take/assign/cancel are staff-only,
# set_outcome accepts only time_chosen onwards)
OPERATOR_STAGES = ("new", "operator_taken")


class AssignIn(BaseModel):
    agent_id: int


class SlotsIn(BaseModel):
    starts: list[datetime]


class OutcomeIn(BaseModel):
    outcome: str
    reason: str | None = None


class ReasonIn(BaseModel):
    reason: str | None = None


def _visible(db: Session, lead_id: int, user: User) -> Lead:
    lead = get_or_404(db, Lead, lead_id, "lead")
    if not L.can_view(lead, user):
        raise forbidden("not your lead")
    return lead


@router.get("/leads")
def leads_list(status: str | None = None, agent_id: int | None = None, operator_id: int | None = None,
               q: str | None = None, archived: bool = False, limit: int = 50, offset: int = 0,
               db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return L.page(db, L.query(db, user, status, agent_id, operator_id, q, archived), user, limit, offset)


@router.get("/leads/board")
def leads_board(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    board = L.board(db, user)
    if user.role not in STAFF_ROLES:
        for st in OPERATOR_STAGES:
            board.pop(st, None)
    return board


@router.get("/leads/counts")
def leads_counts(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    c = L.counts(db, user)
    return c if user.role in STAFF_ROLES else {k: v for k, v in c.items() if k not in OPERATOR_STAGES}


def _num(v) -> str:
    return "" if v is None else f"{float(v):g}"


@router.get("/leads/{lead_id}/contract")
def leads_contract(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Contract generator prefill (frontend ContractWizard CxData keys) from the lead, its object, owner and deal.
    Contacts follow the lead card rules (client after contacts are shared, owner while the lead is active)."""
    lead = _visible(db, lead_id, user)
    d = L.card(db, lead, user)
    prop = db.get(Property, lead.property_id) if lead.property_id else (
        catalog.property_for(db, lead.listing_id))
    listing = db.get(Listing, lead.listing_id) if lead.listing_id else None
    owner = L.owner_contact(db, lead, user, prop) or {}
    deal = db.get(Deal, lead.deal_id) if lead.deal_id else None
    client = d.get("client") or {}
    src = prop or listing
    price = (deal.price if deal and deal.price else None) or (prop.price if prop else listing.price_usd if listing else None)
    sale = bool(deal and deal.deal_type == "sale")
    data = {
        "client_full_name": (deal.client_name if deal else None) or client.get("name") or "",
        "owner_full_name": (deal.owner_name if deal else None) or owner.get("name") or "",
        "cadastral_code": (deal.cadastral if deal else None) or (prop.cadastral_code if prop else None) or "",
        "street_house": (deal.address if deal else None) or (src.address if src else None) or "",
        "floor": str(src.floor) if src and src.floor else "",
        "apartment_area": _num(src.area) if src and src.area else "",
        "sale_price" if sale else "price_usd": _num(price),
    }
    # everything the deal form already has goes into the contract (David 2026-11-08)
    for key, v in (("owner_birth_day", deal and deal.owner_birth), ("client_birth_day", deal and deal.client_birth)):
        if v:
            data[key] = v.isoformat()
    pets = ((client.get("profile") or {}).get("pets") or "none") if isinstance(client, dict) else "none"
    if not sale and pets not in ("none", "", None):
        data["with_pets"] = True
    if not sale:
        data["deposit_usd"] = data["price_usd"]
        if deal and deal.rent_from:
            data["start_rent"] = deal.rent_from.isoformat()
            data["pay_day"] = str(deal.rent_from.day)
        if deal and deal.rent_to:
            data["end_rent"] = deal.rent_to.isoformat()
    return {
        "lead_id": lead.id, "property_id": prop.id if prop else None, "deal_id": deal.id if deal else None,
        "type": "sale" if sale else "comm" if deal and deal.sub_type == "commercial" else "rent",
        "data": data, "district": src.district if src else None,
        "client": {"name": data["client_full_name"] or None, "phone": (deal.client_phone if deal else None) or client.get("phone"),
                   "username": client.get("username")},
        "owner": {"name": data["owner_full_name"] or None, "phone": (deal.owner_phone if deal else None) or owner.get("phone")},
        "contract": contracts.summary(contracts.for_lead(db, lead.id)),
    }


@router.get("/leads/{lead_id}/deal-prefill")
def leads_deal_prefill(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Deal page («Оформить сделку»): the fields of the lead's signed contract; the form's own lead/object prefill
    covers the rest (contacts, agent, object)."""
    lead = _visible(db, lead_id, user)
    c = L.signed_contract(db, lead)
    if c is None:
        return {"contract": None, "fields": {}}
    data = dict(c.data or {})
    for k, v in (("street_house", c.address), ("cadastral_code", c.cadastral), ("client_full_name", c.client_name),
                 ("owner_full_name", c.owner_name)):
        data[k] = data.get(k) or v
    return {"contract": contracts.summary(c), "fields": deals_svc.prefill_from_contract(c.kind, data)}


@router.get("/leads/{lead_id}")
def leads_get(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    lead = _visible(db, lead_id, user)
    d = L.detail(db, lead, user)
    if d.get("contacts_visible") and d.get("client"):  # «Написать клиенту»: tg://user?id= when there is no @username
        c = db.get(Client, lead.client_id)
        d["client"]["tg_id"] = int(c.external_id) if c.channel == "tg" and c.external_id.isdigit() else None
    return d


@router.post("/leads/{lead_id}/take")
def leads_take(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return L.detail(db, L.operator_take(db, lead_id, user), user)


@router.get("/leads/{lead_id}/recommend-agents")
def leads_recommend(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_roles(*STAFF_ROLES))):
    return L.recommend_agents(db, lead_id)


@router.post("/leads/{lead_id}/assign")
def leads_assign(lead_id: int, body: AssignIn, db: Session = Depends(get_db, scope="function"),
                 user: User = Depends(require_roles(*STAFF_ROLES))):
    lead = get_or_404(db, Lead, lead_id, "lead")
    if lead.status in ("new", "operator_taken", "agent_assigned"):
        lead = L.assign_agent(db, lead_id, body.agent_id, user)
    else:
        lead = L.reassign(db, lead_id, body.agent_id, user)
    return L.detail(db, lead, user)


@router.post("/leads/{lead_id}/slots")
def leads_slots(lead_id: int, body: SlotsIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    _visible(db, lead_id, user)
    L.reject_past_slots(body.starts)
    return L.detail(db, L.add_slots(db, lead_id, body.starts, user), user)


@router.post("/leads/{lead_id}/reschedule")
def leads_reschedule(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    _visible(db, lead_id, user)
    return L.detail(db, L.request_reschedule(db, lead_id, "agent" if user.role not in STAFF_ROLES else "operator", user),
                    user)


@router.post("/leads/{lead_id}/outcome")
def leads_outcome(lead_id: int, body: OutcomeIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    _visible(db, lead_id, user)
    return L.detail(db, L.set_outcome(db, lead_id, "agent", body.outcome, body.reason, user), user)


@router.post("/leads/{lead_id}/start-contract")
def leads_start_contract(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    _visible(db, lead_id, user)
    return L.detail(db, L.start_contract(db, lead_id, user), user)


class ArchiveIn(BaseModel):
    archived: bool = True


@router.post("/leads/{lead_id}/archive")
def leads_archive(lead_id: int, body: ArchiveIn, db: Session = Depends(get_db, scope="function"),
                  user: User = Depends(current_user)):
    return L.detail(db, L.set_archived(db, lead_id, user, body.archived), user)


@router.post("/leads/{lead_id}/cancel")
def leads_cancel(lead_id: int, body: ReasonIn, db: Session = Depends(get_db, scope="function"),
                 user: User = Depends(require_roles(*STAFF_ROLES))):
    return L.detail(db, L.cancel(db, lead_id, user, body.reason), user)
