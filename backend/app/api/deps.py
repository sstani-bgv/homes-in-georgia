from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import LEVELS, User, at_least
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


def current_user(db: Session = Depends(get_db, scope="function"), authorization: str | None = Header(None),
                 token: str | None = Query(None, include_in_schema=False)) -> User:
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    raw = raw or token
    if not raw:
        raise DomainError("unauthorized", "missing bearer token", 401)
    payload = decode_token(raw)
    if payload.get("purpose"):
        raise DomainError("unauthorized", "wrong token type", 401)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise DomainError("unauthorized", "user inactive or deleted", 401)
    return user


def require_level(role: str):
    """The user's role is `role` or higher (hierarchical RBAC: a higher role inherits every lower one's rights)."""
    assert role in LEVELS, role

    def dep(user: User = Depends(current_user)) -> User:
        if not at_least(user, role):
            raise DomainError("forbidden", f"requires role {role} or higher")
        return user
    return dep


def paginate(db: Session, stmt, mapper, limit: int = 50, offset: int = 0) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": [mapper(r) for r in rows], "total": total}
