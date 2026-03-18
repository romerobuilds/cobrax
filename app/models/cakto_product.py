from __future__ import annotations

import uuid

from sqlalchemy import Column, String, ForeignKey, DateTime, Numeric, Boolean, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database_.database import Base


class CaktoProduct(Base):
    __tablename__ = "cakto_products"
    __table_args__ = (
        UniqueConstraint("company_id", "cakto_product_id", name="uq_cakto_products_company_external"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    cakto_product_id = Column(String, nullable=False, index=True)

    name = Column(String, nullable=True)
    product_type = Column(String, nullable=True)
    status = Column(String, nullable=True)
    category = Column(String, nullable=True)

    price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String, nullable=True)

    active = Column(Boolean, nullable=True)

    raw_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    company = relationship("Company")