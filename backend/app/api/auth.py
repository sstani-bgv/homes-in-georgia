import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.api.deps import create_token, current_user
from app.db import get_db
from datetime import date

from app.models import User, at_least, is_field
from app.services import invites, users
from app.services.common import now
from app.services.errors import DomainError

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_AGE = 24 * 3600


def verify_webapp_init_data(init_data: str, bot_token: str, max_age: int = MAX_AGE) -> dict:
    """Telegram Mini App initData check: HMAC-SHA256(key=HMAC("WebAppData", bot_token), data_check_string)."""
    pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    got = pairs.pop("hash", None)
    if not got or not bot_token:
        raise DomainError("unauthorized", "bad init data", 401)
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), got):
        raise DomainError("unauthorized", "bad init data signature", 401)
    if time.time() - int(pairs.get("auth_date") or 0) > max_age:
        raise DomainError("unauthorized", "init data expired", 401)
    user = json.loads(pairs.get("user") or "{}")
    if not user.get("id"):
        raise DomainError("unauthorized", "no user in init data", 401)
    return user


def verify_login_widget(data: dict, bot_token: str, max_age: int = MAX_AGE) -> dict:
    """Telegram Login Widget: HMAC-SHA256(key=SHA256(bot_token), data_check_string)."""
    fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
    got = data.get("hash")
    if not got or not bot_token:
        raise DomainError("unauthorized", "bad login data", 401)
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hashlib.sha256(bot_token.encode()).digest()
    if not hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), str(got)):
        raise DomainError("unauthorized", "bad login signature", 401)
    if time.time() - int(fields.get("auth_date") or 0) > max_age:
        raise DomainError("unauthorized", "login data expired", 401)
    return fields


def user_out(u: User, viewer: User | None = None) -> dict:
    """viewer None = the user himself (/me, login). Email and the birth year (age) only to admins and the user."""
    own = viewer is None or viewer.id == u.id or at_least(viewer, "admin")
    bd = u.birth_date
    return {"email": u.email if own else None, "birthday": bd.strftime("%m-%d") if bd else None,
            "profile_missing": users.missing(u), "id": u.id, "tg_id": u.tg_id, "username": u.username, "name": u.name,
            "first_name": u.first_name, "last_name": u.last_name, "phone": u.phone, "role": u.role,
            "level": users.effective_level(u) if is_field(u) else u.level, "mentor_id": u.mentor_id,
            "districts": u.districts, "is_active": u.is_active, "login": u.login, "photo_url": u.photo_url,
            "birth_date": bd.isoformat() if bd and own else None, "exclusive_deals": u.exclusive_deals,
            "plans": u.plans, "agent_tag": u.agent_tag, "works_as_agent": u.works_as_agent, "field": is_field(u), "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}


def _session(db: Session, u: User) -> dict:
    u.last_login_at = now()
    db.commit()
    return {"token": create_token(u), "user": user_out(u)}


def _tg_user(db: Session, tg: dict) -> User:
    u = users.by_tg(db, tg["id"])
    if not u or not u.is_active:
        raise DomainError("not_registered", "Telegram account is not registered on the platform")
    if tg.get("username"):
        u.username = tg["username"]
    if tg.get("photo_url"):
        u.photo_url = tg["photo_url"]
    if not u.name or u.name.startswith("Superadmin "):
        u.name = " ".join(x for x in (tg.get("first_name"), tg.get("last_name")) if x) or u.name
    if not u.birth_date:
        u.birth_date = users.tg_birth_date(u.tg_id)
    return u


class WebAppIn(BaseModel):
    init_data: str


class LoginWidgetIn(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str


class LoginIn(BaseModel):
    login: str
    password: str


@router.post("/telegram-webapp")
def telegram_webapp(body: WebAppIn, db: Session = Depends(get_db, scope="function")):
    data = verify_webapp_init_data(body.init_data, settings.BOT_TOKEN)
    return _session(db, _tg_user(db, data))


@router.post("/telegram-login")
def telegram_login(body: LoginWidgetIn, db: Session = Depends(get_db, scope="function")):
    data = verify_login_widget(body.model_dump(), settings.LOGIN_BOT_TOKEN or settings.BOT_TOKEN)
    return _session(db, _tg_user(db, {**data, "id": int(data["id"])}))


class InviteLoginIn(LoginWidgetIn):
    token: str


@router.get("/invite/{token}")
def invite_info(token: str, db: Session = Depends(get_db, scope="function")):
    """The web invite page (/invite/<token>): enough to say who invites and as whom; no ids, no links."""
    inv = invites.by_token(db, token)
    return {"status": invites.status(inv), "role": inv.role, "invited_by": inv.creator.name if inv.creator else None,
            "expires_at": inv.expires_at.isoformat()}


@router.post("/invite")
def invite_login(body: InviteLoginIn, db: Session = Depends(get_db, scope="function")):
    """«Войти через Telegram» on the web invite page: the Login Widget proves the account, the invite binds it."""
    data = verify_login_widget(body.model_dump(exclude={"token"}), settings.LOGIN_BOT_TOKEN or settings.BOT_TOKEN)
    return _session(db, invites.accept(db, body.token, {**data, "id": int(data["id"])}))


_FAILS: dict[str, list[float]] = {}  # per-process brute-force throttle: key -> failure timestamps
FAIL_WINDOW, FAIL_LIMIT = 15 * 60, 10


def _throttled(*keys: str) -> bool:
    cut = time.time() - FAIL_WINDOW
    for k in keys:
        _FAILS[k] = [t for t in _FAILS.get(k, []) if t > cut]
    return any(len(_FAILS[k]) >= FAIL_LIMIT for k in keys)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db, scope="function")):
    if not settings.PASSWORD_LOGIN:
        raise DomainError("forbidden", "password login is disabled, use Telegram", 403)
    keys = (f"ip:{request.client.host if request.client else '-'}", f"login:{body.login.strip().lower()}")
    if _throttled(*keys):
        raise DomainError("too_many_attempts", "too many attempts, try again in 15 minutes", 429)
    u = users.by_login(db, body.login)
    if not u or not u.is_active or not users.check_password(body.password, u.password_hash):
        for k in keys:
            _FAILS[k].append(time.time())
        raise DomainError("unauthorized", "wrong login or password", 401)
    return _session(db, u)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class ProfileIn(BaseModel):
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    birth_date: date | None = None
    phone: str | None = None
    email: str | None = None


@router.patch("/me")
def patch_me(body: ProfileIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """The user fills his own profile in the cabinet («Заполните профиль»), a field at a time if he likes."""
    return user_out(users.update(db, user.id, body.model_dump(exclude_unset=True), None, full_profile=False))
