"""Bot catalog tables, ported 1:1 from bot/db.py (TEXT timestamps / JSON-as-TEXT kept for the bot SQL)."""
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, PrimaryKeyConstraint,
                        Text, UniqueConstraint, func, text)

from app.db import Base

LEAD_STATUSES = ("new", "operator_taken", "agent_assigned", "slots_sent", "time_chosen", "viewing_done",
                 "deal_pending", "rented", "refused", "thinking", "in_work", "viewing")  # last two: prototype bot


class Setting(Base):
    __tablename__ = "settings"
    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (CheckConstraint("kind IN ('tg_agency', 'tg_competitor', 'fb_group', 'ig_account')", name="sources_kind_check"),)
    id = Column(Integer, primary_key=True)
    kind = Column(Text, nullable=False)
    key = Column(Text, nullable=False, unique=True)
    name = Column(Text)
    url = Column(Text)
    lang = Column(Text)
    options = Column(Text, nullable=False, server_default="{}")
    enabled = Column(Integer, nullable=False, server_default=text("1"))
    position = Column(Integer, nullable=False, server_default=text("0"))


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("status IN ('published', 'pending_review', 'rejected', 'gone')", name="listings_status_check"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="listings_decision_check"),
        Index("listings_status", "status"),
    )
    id = Column(Text, primary_key=True)
    status = Column(Text, nullable=False)
    status_reason = Column(Text)
    decision = Column(Text)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    decided_at = Column(Text)
    review_sent_at = Column(Text)
    source = Column(Text, nullable=False)
    owner_id = Column(Text)
    urls = Column(Text, nullable=False, server_default="[]")
    dup_sources = Column(Text, nullable=False, server_default="[]")
    price_usd = Column(Integer)
    area = Column(Float)
    floor = Column(Integer)
    bedrooms = Column(Integer)
    address = Column(Text)
    district = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    geo_precision = Column(Text)
    photos = Column(Text, nullable=False, server_default="[]")
    description_ru = Column(Text)
    description_en = Column(Text)
    district_en = Column(Text)
    pets = Column(Text)
    renovation = Column(Text)
    style = Column(Text)
    light = Column(Text)
    furniture = Column(Text)
    features = Column(Text, nullable=False, server_default="[]")
    impression = Column(Integer)
    looks = Column(Text)
    agent_reasons = Column(Text, nullable=False, server_default="[]")
    agent_suspect = Column(Integer, nullable=False, server_default=text("0"))
    details = Column(Text, nullable=False, server_default="{}")
    text = Column(Text)
    first_seen = Column(Text, nullable=False)
    last_seen = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="clients_channel_external_id_key"),
                      CheckConstraint("lang IN ('ru', 'en')", name="clients_lang_check"))
    id = Column(Integer, primary_key=True)
    channel = Column(Text, nullable=False)
    external_id = Column(Text, nullable=False)
    chat_id = Column(Text)
    username = Column(Text)
    name = Column(Text)
    phone = Column(Text)
    lang = Column(Text, nullable=False, server_default="ru")
    state = Column(Text, nullable=False, server_default="{}")
    profile = Column(Text, nullable=False, server_default="{}")
    profile_done = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)


class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (PrimaryKeyConstraint("client_id", "listing_id"),
                      CheckConstraint("reaction IN ('like', 'dislike')", name="reactions_reaction_check"))
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    listing_id = Column(Text, ForeignKey("listings.id"), nullable=False)
    reaction = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("client_id", "listing_id", name="leads_client_id_listing_id_key"),
                      CheckConstraint(f"status IN {LEAD_STATUSES}", name="leads_status_check"),
                      Index("leads_status_idx", "status"))
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    listing_id = Column(Text, ForeignKey("listings.id"))
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"))
    status = Column(Text, nullable=False, server_default="new")
    operator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    agent_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    request = Column(Text)
    sla_stage = Column(Text)
    sla_due_at = Column(DateTime(timezone=True))
    viewing_at = Column(DateTime(timezone=True))
    contacts_shared_at = Column(DateTime(timezone=True))
    client_outcome = Column(Text)
    agent_outcome = Column(Text)
    refusal_reason = Column(Text)
    client_note = Column(Text)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="SET NULL", use_alter=True, name="leads_deal_id_fkey"))
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    created_ts = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LeadSlot(Base):
    __tablename__ = "lead_slots"
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    chosen = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LeadEvent(Base):
    __tablename__ = "lead_events"
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(Text, nullable=False)
    agent_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    at = Column(Text, nullable=False)
    kind = Column(Text)
    note = Column(Text)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))


class Search(Base):
    __tablename__ = "searches"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    results = Column(Integer, nullable=False, server_default=text("0"))
    at = Column(Text, nullable=False)


class TgFile(Base):
    __tablename__ = "tg_files"
    path = Column(Text, primary_key=True)
    file_id = Column(Text, nullable=False)
    size = Column(Integer)


class ChannelVideo(Base):
    __tablename__ = "channel_videos"
    __table_args__ = (PrimaryKeyConstraint("chat", "message_id"), Index("channel_videos_group", "chat", "media_group_id"))
    chat = Column(Text, nullable=False)
    message_id = Column(BigInteger, nullable=False)
    media_group_id = Column(Text)
    file_id = Column(Text)
    at = Column(Text, nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (PrimaryKeyConstraint("kind", "ref", "chat_id", "message_id"),)
    kind = Column(Text, nullable=False)
    ref = Column(Text, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=False)


class JourneyAnswer(Base):
    __tablename__ = "journey_answers"
    __table_args__ = (Index("journey_answers_ip", "ip_hash", "created_at"),)
    id = Column(Integer, primary_key=True)
    created_at = Column(Text, nullable=False)
    name = Column(Text)
    payload = Column(Text, nullable=False)
    ip_hash = Column(Text, nullable=False)
