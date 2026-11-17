"""Cars, the site hge.ge's objects that are not real estate (rent and sale objects are `properties`; the old
sale_objects table was copied into properties by migration 0021 and is unused). An agent types the facts by hand
(no funnel, no owner): draft -> published on the site (needs a photo) -> archived; the Telegram post is a separate
target of the same «Опубликовать» (post_url). Photos / video are storage keys in the row itself."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declared_attr

from app.db import Base

OFFER_STATUSES = ("draft", "published", "archived")
CAR_DEALS = ("rent", "sale")


class _Offer:
    id = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False)  # the address (sale) / the model (car)
    description = Column(Text)
    price = Column(Numeric(14, 2))
    currency = Column(Text, nullable=False, server_default="USD")
    lat = Column(Float)
    lng = Column(Float)
    is_exclusive = Column(Boolean, nullable=False, server_default=text("false"))
    photos = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))  # storage keys, in show order (max 9)
    video = Column(Text)  # storage key
    post_url = Column(Text)  # the Telegram post, if any
    site_id = Column(Text, unique=True)  # the old site's id ("homes-3123"): old links resolve to it
    status = Column(Text, nullable=False, server_default="draft")
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    @declared_attr
    def agent_id(cls):
        return Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))


class Car(_Offer, Base):
    __tablename__ = "cars"
    __table_args__ = (Index("cars_status_idx", "status"),
                      CheckConstraint(f"status IN {OFFER_STATUSES}", name="cars_status_check"),
                      CheckConstraint(f"deal IN {CAR_DEALS}", name="cars_deal_check"),
                      Index("cars_tg_post_idx", "tg_channel", "tg_message_id", unique=True))
    deal = Column(Text, nullable=False)  # rent | sale
    period = Column(Text)  # rent: day | month
    deposit = Column(Numeric(12, 2))
    year = Column(Integer)
    gearbox = Column(Text)  # auto | manual
    fuel = Column(Text)
    body = Column(Text)
    seats = Column(Integer)
    mileage = Column(Integer)
    pickup = Column(Text)  # rent: where the car is handed over
    # the post generator's fields beyond the columns (services/car_post.py FIELDS): engine, hp, drive, color, options...
    post_fields = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    source = Column(Text, nullable=False, server_default="crm")  # crm | telegram (services/car_import.py)
    tg_channel = Column(Text)  # the car channel's username of the post it came from / was published to
    tg_message_id = Column(Integer)
    source_text = Column(Text)  # the channel post as read by the importer
