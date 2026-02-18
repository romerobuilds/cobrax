import uuid
from sqlalchemy import Column, String, Text, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database_.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)

    name = Column(Text, nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("email_templates.id"), nullable=False)

    status = Column(Text, nullable=False, default="draft")
    mode = Column(Text, nullable=False, default="selected")  # selected | all | upload

    context = Column(JSONB, nullable=False, default=dict)
    rate_per_min = Column(Integer, nullable=False, default=15)

    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
