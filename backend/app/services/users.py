"""Users, roles, agent levels (apps-script getAgentLevel / getNextLevelAt / promoteRecruit)."""
import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FIELD, ROLES, User
from app.services.common import get_or_404, log_event
from app.services.errors import DomainError

EDITABLE = ("name", "username", "phone", "role", "level", "mentor_id", "districts", "is_active", "login", "photo_url",
            "birth_date", "plans", "tg_id", "exclusive_deals")


def level_for(count: int) -> int:
    return 50 if count < 11 else 70 if count < 101 else 90


def next_level_at(count: int) -> dict | None:
    """{level, deals_left} for the next level, None at the top (Snowflake)."""
    if count < 11:
        return {"level": 70, "at": 11, "deals_left": 11 - count}
    if count < 101:
        return {"level": 90, "at": 101, "deals_left": 101 - count}
    return None


def effective_level(user: User | None) -> int:
    if user is None:
        return 50
    return max(user.level or 50, level_for(user.exclusive_deals or 0))


def level_info(user: User) -> dict:
    n = user.exclusive_deals or 0
    return {"level": effective_level(user), "exclusive_deals": n, "next": next_level_at(n)}


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def by_tg(db: Session, tg_id) -> User | None:
    return db.scalar(select(User).where(User.tg_id == int(tg_id))) if tg_id else None


def by_login(db: Session, login: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.login) == (login or "").strip().lower()))


def _check(data: dict):
    if "role" in data and data["role"] not in ROLES:
        raise DomainError("bad_role", f"role must be one of {ROLES}")
    if data.get("level") is not None and int(data["level"]) not in (50, 70, 90):
        raise DomainError("bad_level", "level must be 50, 70 or 90")


def create(db: Session, data: dict, actor: User | None = None) -> User:
    data = dict(data)
    pw = data.pop("password", None)
    _check(data)
    if actor is not None and actor.role != "superadmin" and data.get("role") in ("superadmin", "admin"):
        raise DomainError("forbidden", "only a superadmin can create admins")
    if not data.get("name"):
        raise DomainError("name_required", "name is required")
    if data.get("tg_id") and by_tg(db, data["tg_id"]):
        raise DomainError("conflict", "tg_id already registered")
    if data.get("login") and by_login(db, data["login"]):
        raise DomainError("conflict", "login already taken")
    u = User(**{k: v for k, v in data.items() if k in EDITABLE})
    if u.role is None:
        u.role = "agent"
    if pw:
        u.password_hash = hash_password(pw)
    db.add(u)
    db.flush()
    log_event(db, "user", u.id, "created", actor, role=u.role)
    db.commit()
    return u


def update(db: Session, uid: int, data: dict, actor: User | None = None) -> User:
    u = get_or_404(db, User, uid, "user")
    data = dict(data)
    pw = data.pop("password", None)
    _check(data)
    if actor is not None and actor.role != "superadmin" and (
            u.role in ("superadmin", "admin") and u.id != actor.id
            or data.get("role") in ("superadmin", "admin") and data.get("role") != u.role):
        raise DomainError("forbidden", "only a superadmin can change admins")
    if data.get("tg_id"):
        other = by_tg(db, data["tg_id"])
        if other is not None and other.id != u.id:
            raise DomainError("conflict", "tg_id already registered")
    if data.get("login"):
        other = by_login(db, data["login"])
        if other is not None and other.id != u.id:
            raise DomainError("conflict", "login already taken")
    changed = {}
    for k, v in data.items():
        if k in EDITABLE and getattr(u, k) != v:
            changed[k] = v
            setattr(u, k, v)
    if pw:
        u.password_hash = hash_password(pw)
        changed["password"] = "***"
    log_event(db, "user", u.id, "updated", actor, **changed)
    db.commit()
    return u


def promote(db: Session, uid: int, level: int = 50, actor: User | None = None) -> User:
    """Recruit → agent (apps-script promoteRecruit)."""
    u = get_or_404(db, User, uid, "user")
    if u.role != "recruit":
        raise DomainError("bad_status", "only recruits can be promoted")
    u.role, u.level = "agent", int(level or 50)
    log_event(db, "user", u.id, "promoted", actor, level=u.level)
    db.commit()
    return u


def list_users(db: Session, role: str | None = None, active: bool | None = None) -> list[User]:
    q = select(User)
    if role:
        q = q.where(User.role.in_(role.split(",")))
    if active is not None:
        q = q.where(User.is_active.is_(active))
    return list(db.scalars(q.order_by(User.name)))


def field_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role.in_(FIELD), User.is_active.is_(True)).order_by(User.name)))


def ensure_bootstrap(db: Session) -> list[User]:
    """Create the bootstrap superadmin(s) from env if the platform has none."""
    created = []
    if db.scalar(select(func.count()).select_from(User).where(User.role == "superadmin")):
        return created
    tg_ids = settings.superadmin_tg_ids
    if settings.ADMIN_BOOTSTRAP_LOGIN and settings.ADMIN_BOOTSTRAP_PASSWORD:
        u = by_login(db, settings.ADMIN_BOOTSTRAP_LOGIN)
        if u:
            u.role, u.is_active = "superadmin", True
        else:
            u = User(name="Superadmin", role="superadmin", login=settings.ADMIN_BOOTSTRAP_LOGIN,
                     password_hash=hash_password(settings.ADMIN_BOOTSTRAP_PASSWORD), tg_id=tg_ids[0] if tg_ids else None)
            db.add(u)
        created.append(u)
        tg_ids = tg_ids[1:] if u.tg_id in tg_ids else tg_ids
    for tg in tg_ids:
        u = by_tg(db, tg)
        if u:
            u.role, u.is_active = "superadmin", True
        else:
            u = User(name=f"Superadmin {tg}", role="superadmin", tg_id=tg)
            db.add(u)
        created.append(u)
    db.commit()
    return created
