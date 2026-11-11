from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func,
                        text)
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base

PROPERTY_STATUSES = ("new", "taken", "call_failed", "owner_confirmed", "agent_marked", "shoot_scheduled",
                     "media_uploaded", "review", "returned", "published", "rented", "archived")
# where we publish an object; plain strings so the site / IG / FB join later without a migration
PUBLICATION_CHANNELS = ("tg",)
PUBLICATION_STATUSES = ("draft", "published", "removed")
# why an object is in the archive (stage 6); duplicate → properties.duplicate_of, other → archive_comment is required
ARCHIVE_REASONS = {"owner_refused": "Собственник отказался", "rented_by_us": "Сдали мы", "rented_by_owner": "Сдал сам",
                   "not_actual": "Неактуально", "duplicate": "Дубль", "other": "Другое",
                   "paused": "На паузе"}  # paused → «Снять с паузы» (properties.resume) puts it back


class Owner(Base):
    __tablename__ = "owners"
    id = Column(Integer, primary_key=True)
    name = Column(Text)
    phone = Column(Text)
    phone_norm = Column(Text, index=True)
    address = Column(Text)
    address_norm = Column(Text)
    link = Column(Text)
    is_agent = Column(Boolean, nullable=False, server_default=text("false"))
    notes = Column(Text)
    agent_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))  # the agent who brought him (stage 6.5)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (Index("properties_status_idx", "status"), Index("properties_agent_idx", "agent_id"))
    id = Column(Integer, primary_key=True)
    listing_id = Column(Text, ForeignKey("listings.id", ondelete="SET NULL"), unique=True)
    source_type = Column(Text)
    source_url = Column(Text)
    source_external_id = Column(Text)
    status = Column(Text, nullable=False, server_default="new")
    owner_id = Column(Integer, ForeignKey("owners.id", ondelete="SET NULL"))
    agent_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    taken_at = Column(DateTime(timezone=True))
    call_attempts = Column(Integer, nullable=False, server_default=text("0"))
    next_call_at = Column(DateTime(timezone=True))
    shoot_at = Column(DateTime(timezone=True))
    # «Сдан» (David 2026-11-10): who rents it and until when, typed by hand for objects whose deal is not in the CRM
    # (the old base); rent_to -> the calendar and an agent reminder CONTRACT_REMIND_DAYS before (migration 0002)
    rent_from = Column(Date)
    rent_to = Column(Date)
    tenant_name = Column(Text)
    tenant_phone = Column(Text)
    tenant_tg = Column(Text)
    address = Column(Text)
    district = Column(Text)
    cadastral_code = Column(Text)
    price = Column(Numeric(12, 2))
    currency = Column(Text, nullable=False, server_default="USD")
    bedrooms = Column(Integer)
    rooms = Column(Integer)
    area = Column(Numeric(10, 2))
    floor = Column(Integer)
    total_floors = Column(Integer)
    description = Column(Text)
    is_exclusive = Column(Boolean, nullable=False, server_default=text("false"))
    checklist = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    post_fields = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))  # services.properties.POST_FIELDS
    ai_flags = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    duplicate_of = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"))
    dup_check = Column(Text)
    review_comment = Column(Text)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    post_url = Column(Text)
    post_message_id = Column(Text)
    # stage 6: the first operator approve / publish; after it the agent changes key fields only via a change request
    verified_at = Column(DateTime(timezone=True))
    archive_reason = Column(Text)  # ARCHIVE_REASONS
    archive_comment = Column(Text)
    archived_at = Column(DateTime(timezone=True))
    # stage 10: «Собственник согласен показать клиентам»: client-eligible with the ad's photos before the publication
    fast_track_at = Column(DateTime(timezone=True))
    fast_track_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    # + text_embedding / photo_embedding vector(768) (migration 0006): not mapped, raw SQL in app/embeddings.py


class PropertyMedia(Base):
    __tablename__ = "property_media"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(Text, nullable=False, server_default="photo")
    path = Column(Text, nullable=False)
    room = Column(Text)
    position = Column(Integer, nullable=False, server_default=text("0"))
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PropertyListing(Base):
    """An outside ad (ss, myhome, fb, old TG post) of this object. Property.listing_id is the origin one."""
    __tablename__ = "property_listings"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id = Column(Text, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True)
    source = Column(Text)
    url = Column(Text)
    first_seen_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True))
    # the operator checked the ad is taken down: no longer an exclusive conflict (migration 0008)
    removed_checked_at = Column(DateTime(timezone=True))
    removed_checked_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ListingMatch(Base):
    """A "similar" object for a new ad (app/services/matching.py), until an operator says same / different."""
    __tablename__ = "listing_matches"
    __table_args__ = (UniqueConstraint("listing_id", "property_id", name="listing_matches_pair_uq"),)
    id = Column(Integer, primary_key=True)
    listing_id = Column(Text, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    score = Column(Float, nullable=False, server_default=text("0"))
    reasons = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status = Column(Text, nullable=False, server_default="pending")  # pending | confirmed | rejected
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    decided_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PropertyPublication(Base):
    """Our post of this object in a channel (PUBLICATION_CHANNELS)."""
    __tablename__ = "property_publications"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(Text, nullable=False, server_default="tg")
    status = Column(Text, nullable=False, server_default="published")
    url = Column(Text)
    message_id = Column(Text)
    published_at = Column(DateTime(timezone=True))
    removed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChangeRequest(Base):
    """«Запрос оператору» (stage 6): an agent's change the operator approves or rejects (app/services/change_requests.py).
    payload: {field: {"old": …, "new": …}} for kind=edit; the kind's own fields otherwise."""
    __tablename__ = "change_requests"
    __table_args__ = (Index("change_requests_status_idx", "status"), Index("change_requests_entity_idx", "entity", "entity_id"))
    id = Column(Integer, primary_key=True)
    entity = Column(Text, nullable=False)  # property | owner
    entity_id = Column(Integer, nullable=False)
    kind = Column(Text, nullable=False)  # edit | archive | transfer_owner | delete
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reason = Column(Text)
    status = Column(Text, nullable=False, server_default="pending")  # pending | approved | rejected
    requested_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    decided_at = Column(DateTime(timezone=True))
    decision_comment = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("events_entity_idx", "entity", "entity_id"),)
    id = Column(Integer, primary_key=True)
    entity = Column(Text, nullable=False)
    entity_id = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    actor_label = Column(Text)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
