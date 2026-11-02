from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db import Base

ROLES = ("superadmin", "admin", "operator", "agent", "recruit")
STAFF = ("superadmin", "admin", "operator")
ADMINS = ("superadmin", "admin")
FIELD = ("agent", "recruit")
DEFAULT_PLANS = {"photo": 15, "showing": 15, "stickers": 15, "rental": 15}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="users_role_check"),)

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True)
    username = Column(Text)
    name = Column(Text, nullable=False)
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
    exclusive_deals = Column(Integer, nullable=False, server_default=text("0"))
    plans = Column(JSONB, nullable=False, server_default=text(
        """'{"photo": 15, "showing": 15, "stickers": 15, "rental": 15}'::jsonb"""))
    legacy_id = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_login_at = Column(DateTime(timezone=True))

    mentor = relationship("User", remote_side=[id])

    @property
    def is_staff(self):
        return self.role in STAFF

    @property
    def is_admin(self):
        return self.role in ADMINS
