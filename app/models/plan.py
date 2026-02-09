# app/models/plan.py
import uuid
from sqlalchemy import Column, String, Integer, DateTime, func, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database_.database import Base


class Plan(Base):
    __tablename__ = "plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    code = Column(Text, unique=True, nullable=False)   # BASIC / PRO / ENTERPRISE
    name = Column(String, nullable=False)

    rate_per_min = Column(Integer, nullable=False, default=20)
    daily_email_limit = Column(Integer, nullable=True)  # NULL = ilimitado

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
