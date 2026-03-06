# app/models/billing_charge.py
from __future__ import annotations

import uuid

from sqlalchemy import Column, String, Numeric, Date, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class BillingCharge(Base):
    __tablename__ = "billing_charges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False, index=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)

    asaas_customer_id = Column(String, nullable=True)
    asaas_payment_id = Column(String, nullable=True, unique=True, index=True)

    value = Column(Numeric(12, 2), nullable=False, default=0)
    status = Column(String, nullable=False, default="PENDING", index=True)

    due_date = Column(Date, nullable=True)

    invoice_url = Column(String, nullable=True)
    bank_slip_url = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    company = relationship("Company")
    campaign = relationship("Campaign")
    client = relationship("Client")