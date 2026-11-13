from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text
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
    scenario = Column(JSONB)  # Molecula 90/10 pick: {axis, opt, role, lev, n}; null = the classic level scheme
    distribution = Column(JSONB)  # [{who: agent|partner|platform|other, role, pct, amount}], 100% of the commission
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


CONTRACT_STATUSES = ("draft", "signed", "deal", "closed_no_deal")
CONTRACT_CLOSE_REASONS = {"deal_failed": "Сделка сорвалась", "client_changed_mind": "Клиент передумал",
                          "owner_refused": "Собственник отказался", "other": "Другое"}


class Contract(Base):
    """A contract generated in the CRM wizard (frontend pages/Contracts.tsx): the document HTML as edited, its PDF
    and the lifecycle draft → signed → deal | closed_no_deal (docs/CONTRACTS.md, migration 0007)."""
    __tablename__ = "contracts"
    __table_args__ = (Index("contracts_lead_idx", "lead_id"), Index("contracts_created_by_idx", "created_by"))
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"))
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="SET NULL"))
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    kind = Column(Text, nullable=False)  # rent | comm | sale
    lang = Column(Text, nullable=False)  # GEO | RU | EN
    title = Column(Text)
    cadastral = Column(Text)
    address = Column(Text)
    client_name = Column(Text)
    owner_name = Column(Text)
    html = Column(Text, nullable=False)
    pdf_path = Column(Text)          # private/contracts/<id>/… generated PDF
    signed_pdf_path = Column(Text)   # the signed scan uploaded at «Договор подписан» (optional)
    status = Column(Text, nullable=False, server_default="draft")
    signed_at = Column(DateTime(timezone=True))
    signed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    close_reason = Column(Text)
    close_comment = Column(Text)
    closed_at = Column(DateTime(timezone=True))
    closed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    share_token = Column(Text, unique=True)
    share_expires_at = Column(DateTime(timezone=True))
    drive_file_id = Column(Text)
    data = Column(JSONB)  # the wizard's fields (frontend CxData): the deal page prefills from the signed contract (0008)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ContractSigner(Base):
    """Electronic signing of a contract (services/esign.py, migration 0009): one row per party — owner | tenant —
    with its own unguessable link /esign/<token>. The party reads the contract, types his name / personal ID / phone and
    signs with a finger; when every party has signed, the signed PDF (signatures on every page + the signing record)
    replaces the scan and the contract becomes «Договор подписан»."""
    __tablename__ = "contract_signers"
    __table_args__ = (Index("contract_signers_contract_idx", "contract_id"),
                      UniqueConstraint("contract_id", "role", name="contract_signers_role_uq"),
                      CheckConstraint("role IN ('owner', 'tenant')", name="contract_signers_role_check"))
    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False)  # owner | tenant
    token = Column(Text, nullable=False, unique=True)
    doc_sha256 = Column(Text, nullable=False)  # of the contract HTML the link was made for: an edit voids the link
    full_name = Column(Text)
    personal_id = Column(Text)
    phone = Column(Text)
    signature = Column(Text)  # data:image/png;base64,… as drawn
    lang = Column(Text)
    ip = Column(Text)
    user_agent = Column(Text)
    signed_at = Column(DateTime(timezone=True))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
