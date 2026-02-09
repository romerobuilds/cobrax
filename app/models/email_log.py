# app/models/email_log.py
import uuid
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("email_templates.id"), nullable=True)

    status = Column(String(20), nullable=False, default="PENDING")

    to_email = Column(Text, nullable=True)
    to_name = Column(Text, nullable=True)

    subject_rendered = Column(Text, nullable=True)
    body_rendered = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    attempt_count = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    # ✅ cancelamento
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    company = relationship("Company", back_populates="email_logs")
    client = relationship("Client", back_populates="email_logs")
    template = relationship("EmailTemplate", back_populates="email_logs")