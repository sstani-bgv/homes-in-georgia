from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base


class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = (Index("deals_month_idx", "month"), Index("deals_agent_idx", "agent_id"),
                      Index("deals_lead_live_uq", "lead_id", unique=True,
                            postgresql_where=text("status <> 'cancelled' AND lead_id IS NOT NULL")),
                      Index("deals_tg_ref_uq", "tg_ref", unique=True, postgresql_where=text("tg_ref IS NOT NULL")))
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"))
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"))
    agent_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    recruit_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deal_type = Column(Text, nullable=False, server_default="rent")
    sub_type = Column(Text)
    is_exclusive = Column(Boolean, nullable=False, server_default=text("false"))
    is_rerented = Column(Boolean, nullable=False, server_default=text("false"))
    price = Column(Numeric(14, 2))
    commission_pct = Column(Numeric(6, 2))
    commission_total = Column(Numeric(14, 2))
    platform_amount = Column(Numeric(14, 2))
    agent_amount = Column(Numeric(14, 2))
    recruit_pct = Column(Numeric(6, 2))
    recruit_amount = Column(Numeric(14, 2))
    client_src = Column(Text)
    client_src_amount = Column(Numeric(14, 2))
    agent_level = Column(Integer)
    agent_deal_num = Column(Integer)
    cadastral = Column(Text, index=True)
    ad_link = Column(Text)
    address = Column(Text)
    metro = Column(Text)
    client_name = Column(Text)
    client_phone = Column(Text)
    client_tg = Column(Text)
    client_birth = Column(Date)
    owner_name = Column(Text)
    owner_phone = Column(Text)
    owner_birth = Column(Date)
    rent_from = Column(Date)
    rent_to = Column(Date)
    status = Column(Text, nullable=False, server_default="pending")
    confirmed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at = Column(DateTime(timezone=True))
    t14_date = Column(Date)
    t14_status = Column(Text, server_default="pending")
    month = Column(Text)
    source = Column(Text, nullable=False, server_default="crm")
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    tg_ref = Column(Text)  # "<chat_id>:<message_id>" of the Telegram #Rented message it was parsed from (idempotency)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Evaluation(Base):
    __tablename__ = "evaluations"
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    kind = Column(Text, nullable=False)
    author = Column(Text, nullable=False, server_default="agent")
    h2o_client = Column(Integer)
    h2o_owner = Column(Integer)
    h2o_process = Column(Integer)
    result = Column(Text)
    changes = Column(Text)
    complaints = Column(Boolean, nullable=False, server_default=text("false"))
    complaints_text = Column(Text)
    comment = Column(Text)
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
