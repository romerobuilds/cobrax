from __future__ import annotations

import uuid

from sqlalchemy import Column, String, ForeignKey, DateTime, Numeric, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database_.database import Base


class CaktoOrder(Base):
    __tablename__ = "cakto_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "cakto_order_id", name="uq_cakto_orders_company_external"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    cakto_order_id = Column(String, nullable=False, index=True)
    cakto_product_id = Column(String, nullable=True, index=True)

    customer_name = Column(String, nullable=True)
    customer_email = Column(String, nullable=True, index=True)
    customer_phone = Column(String, nullable=True)
    doc_number = Column(String, nullable=True)

    status = Column(String, nullable=True)
    payment_method = Column(String, nullable=True)
    amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String, nullable=True)

    offer_type = Column(String, nullable=True)

    utm_source = Column(String, nullable=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True)

    paid_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    order_created_at = Column(DateTime(timezone=True), nullable=True)

    raw_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    company = relationship("Company")