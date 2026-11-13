"""«Посуточная аренда» — the Daily Rent (ApartFlow) app inside the CRM (Irakli 11-13).

The CRM page shows ApartFlow in a frame. POST /daily-rent/session gives the frame its address with a short-lived entry
token (HS256, secret shared with ApartFlow's CRM_SSO_SECRET) in the #fragment, so it never reaches server logs.
Only superadmin, admin and daily_rent_manager get it; ApartFlow checks the signature, audience, expiry and role again.
"""
import time

import jwt
from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.config import settings
from app.models import User, can_daily_rent
from app.services.errors import DomainError

router = APIRouter(prefix="/daily-rent", tags=["daily-rent"])
TTL_SECONDS = 12 * 3600


def entry_token(user: User, now: int | None = None) -> str:
    now = int(now or time.time())
    return jwt.encode({"iss": "hig-crm", "aud": "apartflow", "sub": str(user.id), "name": user.name or "",
                       "role": user.role, "iat": now, "exp": now + TTL_SECONDS},
                      settings.DAILY_RENT_SSO_SECRET, algorithm="HS256")


@router.post("/session")
def session(user: User = Depends(current_user)):
    if not can_daily_rent(user):
        raise DomainError("forbidden", "Daily Rent: superadmin, admin or the Daily Rent manager only")
    if not settings.DAILY_RENT_URL or not settings.DAILY_RENT_SSO_SECRET:
        raise DomainError("not_configured", "Посуточная аренда ещё не подключена к этой CRM", 503)
    return {"url": settings.DAILY_RENT_URL.rstrip("/") + "/#sso=" + entry_token(user), "expires_in": TTL_SECONDS}
