# app/models/billing_charge.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database_.database import Base


class BillingCharge(Base):
    __tablename__ = "billing_charges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)

    asaas_payment_id = Column(String, nullable=False, index=True)

    value = Column(Numeric(12, 2), nullable=False)
    status = Column(String, nullable=False)

    due_date = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    invoice_url = Column(String, nullable=True)
    boleto_pdf_url = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # relações
    company = relationship("Company")
    client = relationship("Client")
    ###