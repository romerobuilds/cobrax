# app/models/billing_charge.py
import uuid
from sqlalchemy import Column, ForeignKey, Date, DateTime, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database_.database import Base


class BillingCharge(Base):
    __tablename__ = "billing_charges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)

    asaas_payment_id = Column(Text, unique=True, nullable=True)

    status = Column(Text, nullable=False, server_default="PENDING")

    value = Column(Numeric(12, 2), nullable=False, server_default="0")
    due_date = Column(Date, nullable=True)

    invoice_url = Column(Text, nullable=True)
    bank_slip_url = Column(Text, nullable=True)
    pdf_url = Column(Text, nullable=True)

    paid_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)