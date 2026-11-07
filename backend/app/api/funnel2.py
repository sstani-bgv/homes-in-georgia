"""Funnel 2: leads."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import STAFF_ROLES, current_user, require_roles
from app.db import get_db
from app.models import Lead, User
from app.services import leads as L
from app.services.common import get_or_404
from app.services.errors import forbidden

router = APIRouter(tags=["leads"])


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
               q: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"),
               user: User = Depends(current_user)):
    return L.page(db, L.query(db, user, status, agent_id, operator_id, q), user, limit, offset)


@router.get("/leads/board")
def leads_board(db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return L.board(db, user)


@router.get("/leads/{lead_id}")
def leads_get(lead_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return L.detail(db, _visible(db, lead_id, user), user)


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


@router.post("/leads/{lead_id}/cancel")
def leads_cancel(lead_id: int, body: ReasonIn, db: Session = Depends(get_db, scope="function"),
                 user: User = Depends(require_roles(*STAFF_ROLES))):
    return L.detail(db, L.cancel(db, lead_id, user, body.reason), user)
