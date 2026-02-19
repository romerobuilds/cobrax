# app/models/email_log.py
import uuid
from sqlalchemy import Column, String, Text, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database_.database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    company = relationship("Company", back_populates="email_logs")
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("email_templates.id"), nullable=True)

    status = Column(String(20), nullable=False)
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

    # ✅ CAMPANHAS (ERA ISSO QUE TAVA FALTANDO NO MODEL)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True)
    campaign_run_id = Column(UUID(as_uuid=True), ForeignKey("campaign_runs.id"), nullable=True)
