"""Users, roles, agent levels (apps-script getAgentLevel / getNextLevelAt / promoteRecruit)."""
import re
from datetime import date

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FIELD_SQL, ROLES, SUPER, User, at_least
from app.services.common import get_or_404, log_event
from app.services.errors import DomainError

EDITABLE = ("name", "first_name", "last_name", "username", "phone", "role", "level", "mentor_id", "districts", "is_active", "login", "photo_url",
            "birth_date", "email", "plans", "tg_id", "exclusive_deals", "agent_tag", "works_as_agent")
# the employee profile Irakli asked for (11-12): «чтобы дни рождения не пропускали»
PROFILE = ("first_name", "last_name", "birth_date", "phone", "email")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}")
TAG_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,31}")
_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяაბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
               "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch _ y _ e yu ya "
               "a b g d e v z t i k l m n o p zh r s t u p k gh q sh ch ts dz ts ch kh j h".split()))


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


def tg_birth_date(tg_id) -> date | None:
    """Birth date from Telegram: initData / the Login Widget don't carry it, only Bot API getChat (ChatFullInfo.birthdate),
    and only if the user's privacy shows it and he gave the year (day + month alone don't make a date). None otherwise."""
    from app.telegram import client as tg
    b = (tg.call("getChat", chat_id=tg_id) or {}).get("birthdate") or {}
    try:
        return date(b["year"], b["month"], b["day"]) if b.get("year") else None
    except (KeyError, TypeError, ValueError):
        return None


def by_tg(db: Session, tg_id) -> User | None:
    return db.scalar(select(User).where(User.tg_id == int(tg_id))) if tg_id else None


def by_login(db: Session, login: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.login) == (login or "").strip().lower()))


# ---------------------------------------------------------------- agent hashtag (stage 6.5)

def _latin(word: str) -> str:
    t = re.sub(r"[^a-z0-9]", "", "".join(_TR.get(c, c) for c in word.lower()).replace("_", ""))
    return t[:1].upper() + t[1:]


def tag_owner(db: Session, tag: str, exclude_id: int | None = None) -> User | None:
    q = select(User).where(func.lower(User.agent_tag) == tag.lstrip("#").lower())
    return db.scalar(q.where(User.id != exclude_id) if exclude_id else q)


def suggest_agent_tag(db: Session, name: str, exclude_id: int | None = None) -> str:
    """The agency rule: the first name in Latin (Zaza); if another agent has that first name, plus the first letter of
    the surname (ArturB); a digit if that is taken too. Without '#'; "" for an empty name."""
    words = [w for w in map(_latin, (name or "").split()) if w]
    if not words:
        return ""
    first = words[0]
    namesakes = [u for u in db.scalars(select(User).where(FIELD_SQL, User.is_active.is_(True)))
                 if u.id != exclude_id and (u.name or "").split() and _latin(u.name.split()[0]).lower() == first.lower()]
    tries = ([] if namesakes else [first]) + ([first + words[1][0]] if len(words) > 1 else [])
    tries += [f"{tries[-1] if tries else first}{n}" for n in range(2, 100)]
    return next(t for t in tries if not tag_owner(db, t, exclude_id))


def clean_agent_tag(db: Session, tag: str | None, uid: int | None = None) -> str | None:
    tag = (tag or "").strip().lstrip("#") or None
    if tag is None:
        return None
    if not TAG_RE.fullmatch(tag):
        raise DomainError("bad_tag", "Хэштег агента: латиница и цифры, с буквы, например #Zaza или #ArturB")
    other = tag_owner(db, tag, uid)
    if other is not None:
        raise DomainError("conflict", f"Хэштег #{tag} уже у агента {other.name}")
    return tag


def tag_of(user: User | None) -> str:
    """The agent's post hashtag: the profile tag, else the first word of the name (pre-6.5 behaviour)."""
    if user is None or not (user.agent_tag or (user.name or "").split()):
        return "#???"
    return f"#{user.agent_tag or user.name.split()[0]}"


def missing(u) -> list[str]:
    """Profile fields still to fill: name (first + last), birth_date, phone, email."""
    get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
    out = [] if get("first_name") and get("last_name") else ["name"]
    return out + [k for k in PROFILE[2:] if not get(k)]


def _clean_profile(data: dict, u: User | None = None):
    """Formats of the profile fields present in data (normalised in place); a required field can't be cleared.
    Names: first_name / last_name; a bare «name» (old callers) is split at the first space; name = first_name."""
    if data.get("name") and "first_name" not in data and "last_name" not in data:
        data["first_name"], _, data["last_name"] = " ".join(str(data["name"]).split()).partition(" ")
    for k in PROFILE:
        if k in data and data[k] is not None and not isinstance(data[k], date):
            data[k] = str(data[k]).strip() or None
        if k in data and data[k] is None:
            raise DomainError("profile_required", f"Поле обязательно: {PROFILE_LABEL[k]}")
    if "first_name" in data or "last_name" in data:
        # name = the first name: the CRM shows only it, the full name is on the agent's card (Irakli 11-13)
        data["name"] = data.get("first_name", u.first_name if u else None) or data.get("last_name", u.last_name if u else None)
    if "phone" in data:
        digits = re.sub(r"\D", "", data["phone"])
        if re.search(r"[^\d\s+()\-.]", data["phone"]) or not 9 <= len(digits) <= 15:
            raise DomainError("bad_phone", "Телефон: номер целиком, например +995 599 123 456")
    if "email" in data:
        data["email"] = data["email"].lower()
        if not EMAIL_RE.fullmatch(data["email"]):
            raise DomainError("bad_email", "Неверный email")
    if "birth_date" in data:
        bd = data["birth_date"] if isinstance(data["birth_date"], date) else date.fromisoformat(data["birth_date"])
        if not date(1920, 1, 1) <= bd <= date.today():
            raise DomainError("bad_birth_date", "Неверная дата рождения")
        data["birth_date"] = bd


PROFILE_LABEL = {"name": "имя и фамилия", "first_name": "имя", "last_name": "фамилия", "birth_date": "дата рождения", "phone": "телефон", "email": "email"}


def _check(data: dict):
    if "role" in data and data["role"] not in ROLES:
        raise DomainError("bad_role", f"role must be one of {ROLES}")
    if data.get("level") is not None and int(data["level"]) not in (50, 70, 90):
        raise DomainError("bad_level", "level must be 50, 70 or 90")


def _guard(db: Session, actor: User | None, u: User | None, data: dict):
    """Irakli's hierarchy: an admin manages operators / agents / recruits (and his own profile) and grants roles up to
    operator; only a superadmin touches admins and superadmins. Never leaves the platform without an active superadmin."""
    role = data.get("role")
    if actor is not None and not at_least(actor, "superadmin"):
        if not at_least(actor, "admin"):
            raise DomainError("forbidden", "only an admin manages users")
        if u is not None and at_least(u, "admin") and u.id != actor.id:
            raise DomainError("forbidden", "only a superadmin can change admins and superadmins")
        if role and at_least(role, "admin") and (u is None or role != u.role):
            raise DomainError("forbidden", "only a superadmin can grant admin roles")
    if u is not None and u.role == "superadmin" and u.is_active and (
            role not in (None, "superadmin") or data.get("is_active") is False):
        others = db.scalar(select(func.count()).select_from(User).where(
            User.role == "superadmin", User.is_active.is_(True), User.id != u.id))
        if not others:
            raise DomainError("last_superadmin", "Это последний активный суперадмин: сначала назначьте другого", 409)


def create(db: Session, data: dict, actor: User | None = None) -> User:
    data = dict(data)
    pw = data.pop("password", None)
    _check(data)
    _guard(db, actor, None, data)
    _clean_profile(data)
    if gaps := missing(data):
        raise DomainError("profile_required", "Заполните: " + ", ".join(PROFILE_LABEL[k] for k in gaps))
    if data.get("tg_id") and by_tg(db, data["tg_id"]):
        raise DomainError("conflict", "tg_id already registered")
    if data.get("login") and by_login(db, data["login"]):
        raise DomainError("conflict", "login already taken")
    if "agent_tag" in data:
        data["agent_tag"] = clean_agent_tag(db, data["agent_tag"])
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


def update(db: Session, uid: int, data: dict, actor: User | None = None, full_profile: bool = True) -> User:
    """full_profile: an edit that touches the profile must leave it complete (the admin's form); the user's own
    cabinet (full_profile=False) may fill it a field at a time. Edits that don't touch it (Активен, role) always pass."""
    u = get_or_404(db, User, uid, "user")
    data = dict(data)
    pw = data.pop("password", None)
    _check(data)
    _guard(db, actor, u, data)
    _clean_profile(data, u)
    if full_profile and any(k in data for k in PROFILE) and (
            gaps := missing({k: data.get(k, getattr(u, k)) for k in PROFILE})):
        raise DomainError("profile_required", "Заполните: " + ", ".join(PROFILE_LABEL[k] for k in gaps))
    if data.get("tg_id"):
        other = by_tg(db, data["tg_id"])
        if other is not None and other.id != u.id:
            raise DomainError("conflict", "tg_id already registered")
    if data.get("login"):
        other = by_login(db, data["login"])
        if other is not None and other.id != u.id:
            raise DomainError("conflict", "login already taken")
    if "agent_tag" in data:
        data["agent_tag"] = clean_agent_tag(db, data["agent_tag"], u.id)
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
    if role:  # comma-separated roles; "field" = everyone who works as an agent (agents, recruits, works_as_agent staff)
        roles = role.split(",")
        q = q.where(User.role.in_(roles) | (FIELD_SQL if "field" in roles else False))
    if active is not None:
        q = q.where(User.is_active.is_(active))
    return list(db.scalars(q.order_by(User.name)))


def field_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(FIELD_SQL, User.is_active.is_(True)).order_by(User.name)))


def ensure_bootstrap(db: Session) -> list[User]:
    """Create the bootstrap superadmin(s) from env if the platform has none."""
    created = []
    if db.scalar(select(func.count()).select_from(User).where(User.role.in_(SUPER))):
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
