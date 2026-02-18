import uuid
from sqlalchemy import Column, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database_.database import Base


class CampaignTarget(Base):
    __tablename__ = "campaign_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)

    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    email = Column(Text, nullable=True)

    payload = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
