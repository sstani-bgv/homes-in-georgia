"""Staff invites (Stanislav 11-12): the admin makes a one-time link, the person opens it in Telegram and that Telegram
account becomes a new user with the invite's role. Single use, expires (TTL_DAYS), revocable, never above what the
inviter may grant (users._guard). The person fills the profile («Заполните профиль») after the first sign-in."""
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Invite, User
from app.services import users
from app.services.common import get_or_404, log_event, now
from app.services.errors import DomainError

TTL_DAYS = 7
FIELDS = ("role", "level", "agent_tag", "districts", "works_as_agent", "note")


def status(inv: Invite) -> str:
    if inv.used_at:
        return "accepted"
    if inv.revoked_at:
        return "revoked"
    return "expired" if inv.expires_at <= now() else "pending"


def links(inv: Invite) -> dict:
    bot = settings.LOGIN_BOT_USERNAME.strip().lstrip("@")
    # only the separate login bot answers /start inv_… (app/telegram/login_bot.py); the client bot doesn't know invites
    return {"bot_link": f"https://t.me/{bot}?start=inv_{inv.token}" if bot and settings.LOGIN_BOT_TOKEN else None,
            "web_link": f"{settings.PUBLIC_URL.rstrip('/')}/invite/{inv.token}"}


def out(inv: Invite) -> dict:
    return {"id": inv.id, "role": inv.role, "level": inv.level, "agent_tag": inv.agent_tag, "districts": inv.districts,
            "works_as_agent": inv.works_as_agent, "note": inv.note, "status": status(inv),
            "created_by": inv.creator.name if inv.creator else None, "created_at": inv.created_at.isoformat(),
            "expires_at": inv.expires_at.isoformat(), "used_at": inv.used_at.isoformat() if inv.used_at else None,
            "used_by": {"id": inv.user.id, "name": inv.user.name} if inv.user else None, **links(inv)}


def create(db: Session, data: dict, actor: User) -> Invite:
    data = {k: v for k, v in data.items() if k in FIELDS}
    data.setdefault("role", "agent")
    users._check(data)
    users._guard(db, actor, None, {"role": data["role"]})  # the inviter's ceiling: admin → up to operator
    data["agent_tag"] = users.clean_agent_tag(db, data.get("agent_tag"))
    inv = Invite(**data, token=secrets.token_urlsafe(24), created_by=actor.id, expires_at=now() + timedelta(days=TTL_DAYS))
    db.add(inv)
    db.flush()
    log_event(db, "invite", inv.id, "created", actor, role=inv.role, note=inv.note)
    db.commit()
    return inv


def list_all(db: Session) -> list[Invite]:
    return list(db.scalars(select(Invite).order_by(Invite.created_at.desc(), Invite.id.desc()).limit(200)))


def revoke(db: Session, iid: int, actor: User) -> Invite:
    inv = get_or_404(db, Invite, iid, "invite", lock=True)
    users._guard(db, actor, None, {"role": inv.role})
    if status(inv) != "pending":
        raise DomainError("bad_status", "Приглашение уже не активно", 409)
    inv.revoked_at = now()
    log_event(db, "invite", inv.id, "revoked", actor)
    db.commit()
    return inv


def by_token(db: Session, token: str, lock: bool = False) -> Invite:
    q = select(Invite).where(Invite.token == (token or "").strip())
    inv = db.scalar(q.with_for_update() if lock else q)
    if inv is None:
        raise DomainError("not_found", "Приглашение не найдено. Попросите новую ссылку", 404)
    return inv


ERRORS = {"accepted": ("invite_used", "Это приглашение уже использовано", 409),
          "revoked": ("invite_revoked", "Приглашение отозвано. Попросите новую ссылку", 410),
          "expired": ("invite_expired", "Срок приглашения истёк. Попросите новую ссылку", 410)}


def accept(db: Session, token: str, tg: dict) -> User:
    """tg: a verified Telegram user (id, username, first_name, last_name, photo_url?). Creates the user, burns the token."""
    inv = by_token(db, token, lock=True)
    if (st := status(inv)) != "pending":
        raise DomainError(*ERRORS[st])
    tg_id = int(tg["id"])
    if (other := users.by_tg(db, tg_id)) is not None:
        raise DomainError("tg_already_bound", f"Этот Telegram уже привязан к аккаунту «{other.name}». "
                          "Откройте ссылку из другого Telegram или просто войдите в CRM", 409)
    name = " ".join(x for x in (tg.get("first_name"), tg.get("last_name")) if x) or tg.get("username") or f"Сотрудник {tg_id}"
    u = User(tg_id=tg_id, username=tg.get("username"), name=name.strip(), photo_url=tg.get("photo_url"), role=inv.role,
             level=inv.level, districts=inv.districts or [], works_as_agent=inv.works_as_agent,
             agent_tag=users.clean_agent_tag(db, inv.agent_tag) if inv.agent_tag else None)
    db.add(u)
    db.flush()
    inv.used_at, inv.used_by = now(), u.id
    actor = inv.creator
    log_event(db, "user", u.id, "created", actor, role=u.role, via="invite", invite=inv.id, tg_id=tg_id)
    log_event(db, "invite", inv.id, "accepted", None, u.name, user_id=u.id, tg_id=tg_id, username=u.username)
    db.commit()
    return u
