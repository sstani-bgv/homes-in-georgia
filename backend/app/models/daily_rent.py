"""«Посуточная аренда» (Daily Rent): ApartFlow's data inside the CRM (was a separate Node + SQLite app, Irakli 11-13).

Apartments (with their owner and the 20/80 split), stays (bookings) with their expenses, cleanings, staff, the busy-date
calendar (blocks: manual closures and the dates imported from Airbnb / Booking iCal feeds) and the feeds themselves
(channels). Nothing is deleted physically: deleted_at, as in ApartFlow. Every change goes to `events`
(entity dr_<table>, data before/after) — ApartFlow's audit_log. Money: services/daily_rent.py calc()."""
from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, func,
                        text)
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base

DR_PLATFORMS = ("Airbnb", "Booking", "Другое")
DR_PAYMENT = ("received", "pending", "partial")
DR_STATUS = ("free", "busy", "clean", "tech")
DR_CHANNELS = ("airbnb", "booking", "other")
DR_BLOCK_SOURCES = ("manual", "airbnb", "booking", "other")
DR_EXPENSE_CATS = ("cleaning", "supplies", "repair", "other", "telmico", "gwp", "energy", "internet", "tbilisi_clean")


class _Row:
    id = Column(Integer, primary_key=True)
    legacy_id = Column(Text, unique=True)  # ApartFlow's id: the import is re-runnable
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True))


class DrApartment(_Row, Base):
    __tablename__ = "dr_apartments"
    __table_args__ = (CheckConstraint(f"status IN {DR_STATUS}", name="dr_apartments_status_check"),
                      CheckConstraint("platform_pct >= 0 AND platform_pct <= 100", name="dr_apartments_pct_check"),
                      CheckConstraint("report_lang IN ('ru', 'en', 'ka')", name="dr_apartments_report_lang_check"))
    name = Column(Text, nullable=False)
    address = Column(Text)
    owner_name = Column(Text)
    owner_contact = Column(Text)
    owner_account = Column(Text)  # IBAN for the payout
    platform_pct = Column(Numeric(5, 2), nullable=False, server_default="20")  # owner gets 100 - platform_pct
    platforms = Column(JSONB, nullable=False, server_default=text("""'["Airbnb", "Booking"]'::jsonb"""))
    status = Column(Text, nullable=False, server_default="free")  # free | busy | clean | tech
    active = Column(Boolean, nullable=False, server_default=text("true"))
    rooms = Column(Integer)
    notes = Column(Text)
    util_rates = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))  # monthly bills, GEL: telmico, gwp, ...
    # for the site (hge.ge, later): photos (storage keys, show order), the nightly price, a description
    photos = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    price_night = Column(Numeric(12, 2))
    currency = Column(Text, nullable=False, server_default="USD")
    description = Column(Text)
    district = Column(Text)
    report_lang = Column(Text, nullable=False, server_default="ru")  # the owner's reports: ru | en | ka


class DrBooking(_Row, Base):
    __tablename__ = "dr_bookings"
    __table_args__ = (Index("dr_bookings_apt_idx", "apartment_id", "check_in"),
                      CheckConstraint(f"payment_status IN {DR_PAYMENT}", name="dr_bookings_payment_check"),
                      CheckConstraint("currency IN ('USD', 'GEL')", name="dr_bookings_currency_check"))
    apartment_id = Column(Integer, ForeignKey("dr_apartments.id", ondelete="RESTRICT"), nullable=False)
    platform = Column(Text, nullable=False, server_default="Airbnb")  # Airbnb | Booking | Другое
    guest_name = Column(Text)
    check_in = Column(Date)
    check_out = Column(Date)
    gross = Column(Numeric(12, 2))  # what came in, in `currency`
    currency = Column(Text, nullable=False, server_default="USD")
    tax_pct = Column(Numeric(5, 2), nullable=False, server_default="0")
    booking_fee = Column(Numeric(12, 2))  # Booking.com only; goes to the platform's share
    platform_pct = Column(Numeric(5, 2), nullable=False, server_default="20")  # the apartment's split at the time
    payment_status = Column(Text, nullable=False, server_default="received")
    paid_out = Column(Boolean, nullable=False, server_default=text("false"))  # the owner got his share
    paid_out_at = Column(Date)
    assigned_staff = Column(Text)
    comment = Column(Text)
    extra = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))  # legacy fields with no column, kept as is


class DrExpense(_Row, Base):
    __tablename__ = "dr_expenses"
    __table_args__ = (Index("dr_expenses_booking_idx", "booking_id"),
                      CheckConstraint("payer IN ('platform', 'owner')", name="dr_expenses_payer_check"))
    booking_id = Column(Integer, ForeignKey("dr_bookings.id", ondelete="RESTRICT"))
    apartment_id = Column(Integer, ForeignKey("dr_apartments.id", ondelete="RESTRICT"))
    category = Column(Text, nullable=False, server_default="other")
    amount = Column(Numeric(12, 2))
    currency = Column(Text, nullable=False, server_default="GEL")
    payer = Column(Text, nullable=False, server_default="platform")  # who paid: compensated from the net
    comment = Column(Text)
    date = Column(Date)
    created_by = Column(Text)
    extra = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class DrCleaning(_Row, Base):
    __tablename__ = "dr_cleanings"
    apartment_id = Column(Integer, ForeignKey("dr_apartments.id", ondelete="RESTRICT"), nullable=False)
    cleaner = Column(Text)
    date = Column(Date)
    started_at = Column(Text)  # "HH:MM"
    finished_at = Column(Text)
    hours = Column(Numeric(6, 2))
    hourly_rate = Column(Numeric(8, 2))
    amount = Column(Numeric(10, 2))  # GEL
    notes = Column(Text)


class DrStaff(_Row, Base):
    __tablename__ = "dr_staff"
    name = Column(Text, nullable=False)
    role = Column(Text)
    contact = Column(Text)
    active = Column(Boolean, nullable=False, server_default=text("true"))


class DrChannel(_Row, Base):
    """One apartment on one platform: its iCal feed to import, and our feed (export_token) the platform polls."""
    __tablename__ = "dr_channels"
    __table_args__ = (CheckConstraint(f"channel IN {DR_CHANNELS}", name="dr_channels_channel_check"),)
    apartment_id = Column(Integer, ForeignKey("dr_apartments.id", ondelete="RESTRICT"), nullable=False)
    channel = Column(Text, nullable=False)
    import_url = Column(Text)
    export_token = Column(Text, nullable=False, unique=True)
    last_sync_at = Column(DateTime(timezone=True))
    last_status = Column(Text)  # ok | error
    last_error = Column(Text)
    events = Column(Integer)


class DrBlock(_Row, Base):
    """Busy dates that are not our stays: a manual closure or a platform's event. Half-open [start, end)."""
    __tablename__ = "dr_blocks"
    __table_args__ = (Index("dr_blocks_apt_idx", "apartment_id", "start"),
                      CheckConstraint(f"source IN {DR_BLOCK_SOURCES}", name="dr_blocks_source_check"))
    apartment_id = Column(Integer, ForeignKey("dr_apartments.id", ondelete="RESTRICT"), nullable=False)
    channel_id = Column(Integer, ForeignKey("dr_channels.id", ondelete="SET NULL"))
    source = Column(Text, nullable=False)
    start = Column(Date, nullable=False)
    end = Column(Date, nullable=False)
    reason = Column(Text)
    summary = Column(Text)
    external_uid = Column(Text)
    reserved = Column(Boolean, nullable=False, server_default=text("true"))  # Airbnb «Not available» = false
    status = Column(Text, nullable=False, server_default="active")  # active | cancelled
    cancelled_at = Column(DateTime(timezone=True))
