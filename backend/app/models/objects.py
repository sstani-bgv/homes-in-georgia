from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base

PROPERTY_STATUSES = ("new", "taken", "call_failed", "owner_confirmed", "agent_marked", "shoot_scheduled",
                     "media_uploaded", "review", "returned", "published", "rented", "archived")
# where we publish an object; plain strings so the site / IG / FB join later without a migration
PUBLICATION_CHANNELS = ("tg",)
PUBLICATION_STATUSES = ("draft", "published", "removed")


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
    ai_flags = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    duplicate_of = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"))
    dup_check = Column(Text)
    review_comment = Column(Text)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    post_url = Column(Text)
    post_message_id = Column(Text)
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
