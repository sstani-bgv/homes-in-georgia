from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Text, func

from app.db import Base


class AgentNda(Base):
    """A new agent's NDA (services/nda.py, docs/NDA.md, migration 0017): staff fill in the agent, the company's signature
    (drawn once by David) goes in by itself, the agent opens /nda/<token>, confirms himself with Telegram and signs with
    a finger. The text is frozen when the NDA is made (doc_sha256)."""
    __tablename__ = "agent_ndas"
    __table_args__ = (Index("agent_ndas_created_idx", "created_at"),
                      CheckConstraint("status IN ('sent', 'signed', 'cancelled')", name="agent_ndas_status_check"),
                      CheckConstraint("lang IN ('KA', 'KA-RU', 'KA-EN')", name="agent_ndas_lang_check"))
    id = Column(Integer, primary_key=True)
    lang = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default="sent")
    token = Column(Text, nullable=False, unique=True)
    # the recipient as staff entered him (printed in the text)
    full_name = Column(Text, nullable=False)
    personal_id = Column(Text, nullable=False)
    phone = Column(Text)
    telegram = Column(Text)  # @username without «@»: only this account may sign; empty = any confirmed account
    role = Column(Text)
    html = Column(Text, nullable=False)
    doc_sha256 = Column(Text, nullable=False)
    company_signature = Column(Text)  # data:image/png at the moment the NDA was made
    company_signed_at = Column(DateTime(timezone=True))
    # Telegram confirmation (Login Widget, verified with the bot token) and the signature
    tg_id = Column(Text)
    tg_username = Column(Text)
    tg_name = Column(Text)
    tg_auth_date = Column(DateTime(timezone=True))
    tg_hash = Column(Text)  # the widget's signed hash: the «confirmation code» of the NDA's clause 5
    signature = Column(Text)
    ip = Column(Text)
    user_agent = Column(Text)
    signed_at = Column(DateTime(timezone=True))
    pdf_path = Column(Text)
    drive_file_id = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    cancelled_at = Column(DateTime(timezone=True))
