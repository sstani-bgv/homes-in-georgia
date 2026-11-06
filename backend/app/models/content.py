import html
import re

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base

REPORT_KINDS = ("photo", "showing", "stickers", "daily", "general", "makefoto", "calc", "assigned_showing")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("reports_kind_date_idx", "kind", "date"),)
    id = Column(Integer, primary_key=True)
    kind = Column(Text, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    recruit_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    mentor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    date = Column(Date)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def posts(self) -> list[dict]:
        """[{topic, text}] Telegram-HTML texts built by services.reports (apps-script notifyTopic texts)."""
        return (self.data or {}).get("posts") or []

    @property
    def text(self) -> str | None:
        """Plain-text version of the posts (for consumers that escape HTML themselves)."""
        if not self.posts:
            return None
        return html.unescape(re.sub(r"<[^>]+>", "", "\n\n".join(p["text"] for p in self.posts)))


class TickerMessage(Base):
    __tablename__ = "ticker_messages"
    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default="active")
    legacy_id = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True))
    text = Column(Text)
    message_id = Column(Text)


class Address(Base):
    __tablename__ = "addresses"
    id = Column(Integer, primary_key=True)
    district = Column(Text)
    address = Column(Text)
    ad_link = Column(Text)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    legacy_id = Column(Text)


class ChannelPost(Base):
    __tablename__ = "channel_posts"
    id = Column(Integer, primary_key=True)
    message_id = Column(Text, unique=True)
    date = Column(DateTime(timezone=True))
    agent_name = Column(Text)
    agent_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    district = Column(Text)
    price = Column(Numeric(14, 2))
    rooms = Column(Text)
    building = Column(Text)
    is_exclusive = Column(Boolean, nullable=False, server_default=text("false"))
    link = Column(Text)
    raw = Column(Text)
    status = Column(Text, nullable=False, server_default="active")
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), index=True)  # our object, if matched


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("jobs_due_idx", "done_at", "due_at"), Index("jobs_kind_ref_idx", "kind", "ref"))
    id = Column(Integer, primary_key=True)
    kind = Column(Text, nullable=False)
    ref = Column(Text, nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    done_at = Column(DateTime(timezone=True))
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    last_error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
