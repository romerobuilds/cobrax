# app/models/email_log.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base  # <-- se no seu projeto for outro caminho, ajuste aqui
from sqlalchemy import Boolean

class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # FKs
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("email_templates.id"), nullable=True)

    # ✅ campanhas
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True)
    campaign_run_id = Column(UUID(as_uuid=True), ForeignKey("campaign_runs.id"), nullable=True)

    # Campos do envio
    status = Column(String(20), nullable=False)  # QUEUED | SENDING | SENT | FAILED | RETRYING | ...
    to_email = Column(Text, nullable=True)
    to_name = Column(Text, nullable=True)
    subject_rendered = Column(Text, nullable=True)
    body_rendered = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    attempt_count = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    asaas_bank_slip_url = Column(Text, nullable=True)
    should_attach_pdf = Column(Boolean, nullable=False, server_default="false")

    # =========================
    # RELATIONSHIPS (IMPORTANTE!)
    # =========================
    company = relationship("Company", back_populates="email_logs")

    # ✅ isso resolve o erro atual ("no property 'client'")
    client = relationship("Client", back_populates="email_logs")

    # ✅ boa prática: template também
    template = relationship("EmailTemplate", back_populates="email_logs")

    # ✅ campanhas
    campaign = relationship("Campaign", back_populates="email_logs")
    campaign_run = relationship("CampaignRun", back_populates="email_logs")

