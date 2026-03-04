# app/models/billing_charge.py
import uuid
from sqlalchemy import Column, String, Numeric, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class BillingCharge(Base):
    __tablename__ = "billing_charges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False, index=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)

    asaas_customer_id = Column(Text, nullable=True)
    asaas_payment_id = Column(Text, nullable=True, unique=True)

    value = Column(Numeric(12, 2), nullable=False, default=0)
    status = Column(Text, nullable=False, default="PENDING")

    # no banco é DATE (não DateTime)
    due_date = Column(Date, nullable=True)

    invoice_url = Column(Text, nullable=True)
    bank_slip_url = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # relações
    company = relationship("Company")
    client = relationship("Client")
    campaign = relationship("Campaign")