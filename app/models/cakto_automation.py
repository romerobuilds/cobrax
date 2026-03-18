from __future__ import annotations

import uuid

from sqlalchemy import Column, String, ForeignKey, DateTime, Boolean, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class CaktoAutomation(Base):
    __tablename__ = "cakto_automations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(150), nullable=False)

    is_active = Column(Boolean, nullable=False, server_default="true")

    # Fase 1
    event_type = Column(String(50), nullable=False, server_default="order_paid")
    action_type = Column(String(50), nullable=False, server_default="sync_customer")

    # filtro opcional por produto da Cakto
    cakto_product_id = Column(String(120), nullable=True)

    # regra simples desta fase
    run_on_status_paid = Column(Boolean, nullable=False, server_default="true")

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    last_run_at = Column(DateTime(timezone=True), nullable=True)

    company = relationship("Company")