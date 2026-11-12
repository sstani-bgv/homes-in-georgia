from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, validates

from app.db import Base

# Hierarchical RBAC (Irakli 2026-11-12, docs/ROLES-IRAKLI-2026-11-12.md): every role inherits all rights of the lower
# ones, so access checks compare levels (at_least(u, "operator")), never role lists.
LEVELS = {"recruit": 1, "agent": 2, "operator": 3, "admin": 4, "superadmin": 5}
ROLES = ("superadmin", "admin", "operator", "agent", "recruit")
SUPER = ("superadmin",)
ADMINS = SUPER + ("admin",)
STAFF = ADMINS + ("operator",)
# «+ как агент»: agents/recruits always work in the field; a staff member only with users.works_as_agent (David, Irakli).
# FIELD = the roles that are field by role alone; is_field(u) / FIELD_SQL add the flag.
FIELD = ("agent", "recruit")
DEFAULT_PLANS = {"photo": 15, "showing": 15, "stickers": 15, "rental": 15}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="users_role_check"),)

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True)
    username = Column(Text)
    name = Column(Text, nullable=False)  # «first last», kept in sync by the users service (migration 0012)
    first_name = Column(Text)
    last_name = Column(Text)
    phone = Column(Text)
    role = Column(Text, nullable=False)
    level = Column(Integer)
    mentor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    districts = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    login = Column(Text, unique=True)
    password_hash = Column(Text)
    photo_url = Column(Text)
    birth_date = Column(Date)
    email = Column(Text)  # admins and the user only (migration 0010)
    exclusive_deals = Column(Integer, nullable=False, server_default=text("0"))
    plans = Column(JSONB, nullable=False, server_default=text(
        """'{"photo": 15, "showing": 15, "stickers": 15, "rental": 15}'::jsonb"""))
    legacy_id = Column(Text)
    works_as_agent = Column(Boolean, nullable=False, server_default=text("false"))  # staff «+ как агент» (migration 0009)
    agent_tag = Column(Text)  # post hashtag without '#' (Zaza, ArturB); unique lower(agent_tag) index (migration 0001)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_login_at = Column(DateTime(timezone=True))

    mentor = relationship("User", remote_side=[id])

    @validates("name")
    def _split_name(self, _key, name):
        """A name set without first/last (Telegram, imports, old API callers) fills them at the first space."""
        if name and not (self.first_name or self.last_name):
            self.first_name, _, last = name.strip().partition(" ")
            self.last_name = last.strip() or None
        return name

    @property
    def is_staff(self):
        return at_least(self, "operator")

    @property
    def is_admin(self):
        return at_least(self, "admin")


def role_level(u) -> int:
    """User or role name → 1 (recruit) .. 5 (superadmin); 0 for None / unknown."""
    return LEVELS.get(getattr(u, "role", u), 0)


def at_least(u, role: str) -> bool:
    return u is not None and role_level(u) >= LEVELS[role]


def is_field(u) -> bool:
    """Works as an agent: own objects/leads, agent pickers, auto-assignment, leaderboard, level/tag."""
    return u is not None and (u.role in FIELD or bool(getattr(u, "works_as_agent", False)))


FIELD_SQL = User.role.in_(FIELD) | User.works_as_agent.is_(True)


class Invite(Base):
    """One-time staff invite (Stanislav 11-12): t.me/<login bot>?start=inv_<token> or <PUBLIC_URL>/invite/<token>
    binds the Telegram account that opens it to a new user with these role settings (services/invites.py)."""
    __tablename__ = "invites"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="invites_role_check"),)

    id = Column(Integer, primary_key=True)
    token = Column(Text, unique=True, nullable=False)
    role = Column(Text, nullable=False)
    level = Column(Integer)
    agent_tag = Column(Text)
    districts = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    works_as_agent = Column(Boolean, nullable=False, server_default=text("false"))
    note = Column(Text)  # who it is for, as the admin wrote it
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    used_at = Column(DateTime(timezone=True))
    used_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    creator = relationship("User", foreign_keys=[created_by])
    user = relationship("User", foreign_keys=[used_by])
