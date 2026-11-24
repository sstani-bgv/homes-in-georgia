from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import CARS_ONLY, DAILY_RENT_ONLY, LEVELS, User, at_least
from app.services.errors import DomainError

ALG = "HS256"


def create_token(user: User, ttl_hours: float | None = None, **extra) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=ttl_hours or settings.JWT_TTL_HOURS)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp, **extra}, settings.SECRET_KEY, algorithm=ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALG])
    except jwt.PyJWTError:
        raise DomainError("unauthorized", "invalid or expired token", 401)


# The only CRM API a Daily Rent manager may call (Irakli 11-13): everything else is refused for that role here, in the
# one dependency every endpoint goes through — so a new endpoint is closed to it by default.
DAILY_RENT_PATHS = ("/api/auth/me", "/api/auth/media-token")  # + the token its photos load with
DAILY_RENT_PREFIX = ("/api/daily-rent/", "/api/media/daily/")  # its section and the apartments' photos
# «Оператор авто» (Stanislav 11-16): the same, for the cars (api/offers.py) and their photos
CARS_PREFIX = ("/api/offers/car", "/api/media/offers/")


# <img> / <video> / <a href> can't send the Authorization header, so file URLs carry ?token=. Not the session JWT (a
# copied photo link would open the whole CRM for 72 h): a short-lived purpose="media" token (GET /api/auth/media-token),
# taken ONLY from the query and ONLY for GET of the file endpoints below — the one the frontend's mediaUrl() builds
# (content.media_file; listing photos and the /public/ PDFs need no login, contract PDFs go by fetch + header).
MEDIA_TOKEN_TTL_HOURS = 8
MEDIA_TOKEN_PREFIX = ("/api/media/",)


def create_media_token(user: User) -> str:
    return create_token(user, MEDIA_TOKEN_TTL_HOURS, purpose="media")


def current_user(request: Request, db: Session = Depends(get_db, scope="function"), authorization: str | None = Header(None),
                 token: str | None = Query(None, include_in_schema=False)) -> User:
    raw, from_query = None, False
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw and token:
        raw, from_query = token, True
    if not raw:
        raise DomainError("unauthorized", "missing bearer token", 401)
    payload = decode_token(raw)
    if from_query:  # a media token for a file, nothing else (the session token in a URL is refused too)
        path = request.url.path
        if payload.get("purpose") != "media" or request.method != "GET" or not path.startswith(MEDIA_TOKEN_PREFIX) or ".." in path:
            raise DomainError("unauthorized", "wrong token type", 401)
    elif payload.get("purpose"):
        raise DomainError("unauthorized", "wrong token type", 401)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise DomainError("unauthorized", "user inactive or deleted", 401)
    request.state.user = user  # the request log line names who it was (main.py)
    if user.role == DAILY_RENT_ONLY:
        path = request.url.path
        if (path not in DAILY_RENT_PATHS and not path.startswith(DAILY_RENT_PREFIX)) or ".." in path:
            raise DomainError("forbidden", "the Daily Rent manager sees only Daily Rent")
    if user.role == CARS_ONLY:
        path = request.url.path
        if (path not in DAILY_RENT_PATHS and not path.startswith(CARS_PREFIX)) or ".." in path:
            raise DomainError("forbidden", "the car operator sees only the cars")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not at_least(user, "operator"):
        deal_lock(db, user, request.url.path)
    return user


# The deal is overdue (services/contracts.overdue_for, David 11-23): the agent may read, but change only what enters
# that deal — the deal form, the contract itself, his profile / logout.
DEAL_LOCK_FREE = ("/api/auth/", "/api/deals", "/api/evaluations")


def deal_lock(db: Session, user: User, path: str) -> None:
    from app.services import contracts as C
    if path.startswith(DEAL_LOCK_FREE) or ".." in path:
        return
    c = C.overdue_for(db, user)
    if c and not path.startswith(f"/api/contracts/{c.id}/"):
        raise DomainError(C.DEAL_OVERDUE, f"Сначала оформите сделку по договору #{c.id}" + (f" ({c.address})" if c.address else ""),
                          423, {"contract": C.overdue_out(c)})


def require_level(role: str):
    """The user's role is `role` or higher (hierarchical RBAC: a higher role inherits every lower one's rights)."""
    assert role in LEVELS, role

    def dep(user: User = Depends(current_user)) -> User:
        if not at_least(user, role):
            raise DomainError("forbidden", f"requires role {role} or higher")
        return user
    return dep


def require(section: str, action: str):
    """The user's «Права» flag (services/perms.py): the section-by-section successor of require_level."""
    from app.services import perms
    assert section in perms.SECTIONS and action in perms.ACTIONS, (section, action)

    def dep(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> User:
        perms.check(user, section, action)
        return user
    return dep


def paginate(db: Session, stmt, mapper, limit: int = 50, offset: int = 0) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": [mapper(r) for r in rows], "total": total}
