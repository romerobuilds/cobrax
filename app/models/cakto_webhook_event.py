from __future__ import annotations

import uuid

from sqlalchemy import Column, String, ForeignKey, DateTime, Text, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database_.database import Base


class CaktoWebhookEvent(Base):
    __tablename__ = "cakto_webhook_events"
    __table_args__ = (
        UniqueConstraint("company_id", "dedupe_key", name="uq_cakto_webhook_events_company_dedupe"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    webhook_token = Column(String(80), nullable=False)
    dedupe_key = Column(String(255), nullable=False)

    event_name = Column(String(100), nullable=True)
    external_event_id = Column(String(120), nullable=True)
    external_order_id = Column(String(120), nullable=True)

    status = Column(String(30), nullable=False, server_default="RECEIVED", index=True)
    payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    company = relationship("Company")