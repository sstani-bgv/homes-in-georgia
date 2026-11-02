from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
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


def require_roles(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise DomainError("forbidden", f"requires role {', '.join(roles)}")
        return user
    return dep


STAFF_ROLES = ("superadmin", "admin", "operator")
ADMIN_ROLES = ("superadmin", "admin")
AGENT_PLUS = ("superadmin", "admin", "operator", "agent")


def paginate(db: Session, stmt, mapper, limit: int = 50, offset: int = 0) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": [mapper(r) for r in rows], "total": total}
