# app/models/campaign.py
import uuid
from sqlalchemy import Column, Text, Integer, ForeignKey, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database_.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)

    name = Column(Text, nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("email_templates.id"), nullable=False)

    status = Column(Text, nullable=False, default="draft")
    mode = Column(Text, nullable=False, default="selected")  # selected | all | upload

    context = Column(JSONB, nullable=False, default=dict)
    rate_per_min = Column(Integer, nullable=False, default=15)

    # =========================
    # LEGACY (one-shot scheduling)
    # =========================
    scheduled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # =========================
    # NOVO: scheduling + repetição
    # =========================
    is_schedule_enabled = Column(Boolean, nullable=False, server_default="false")

    start_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)

    end_at = Column(DateTime(timezone=True), nullable=True)
    max_occurrences = Column(Integer, nullable=True)

    occurrences = Column(Integer, nullable=False, server_default="0")

    # repetição: none | minutes | hours | days | weeks
    repeat_type = Column(Text, nullable=False, server_default="none")
    repeat_every = Column(Integer, nullable=False, server_default="0")

    # para repeat_type=weeks: "0,1,2,3,4,5,6" (Mon=0..Sun=6)
    repeat_weekdays = Column(Text, nullable=True)

    timezone = Column(Text, nullable=False, server_default="America/Sao_Paulo")

    # =========================
    # NOVO: cobrança / boletos
    # =========================
    is_cobranca = Column(Boolean, nullable=False, server_default="false")
    emitir_boletos = Column(Boolean, nullable=False, server_default="false")
    anexar_pdf = Column(Boolean, nullable=False, server_default="false")
    stop_on_paid = Column(Boolean, nullable=False, server_default="true")
    boleto_due_days = Column(Integer, nullable=False, server_default="3")

    email_logs = relationship(
        "EmailLog",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )