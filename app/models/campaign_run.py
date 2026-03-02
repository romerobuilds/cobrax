# app/models/campaign_run.py
import uuid
from sqlalchemy import Column, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database_.database import Base
from sqlalchemy.orm import relationship


class CampaignRun(Base):
    __tablename__ = "campaign_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    campaign_id = Column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )

    # auditoria / idempotência do scheduler
    # scheduled_fire_at = "o horário que esse run representa" (scheduled_at ou next_run_at no momento do disparo)
    scheduled_fire_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # opcional: de onde veio o run
    # manual | scheduled_at | recurring
    triggered_by = Column(Text, nullable=False, server_default="manual")

    status = Column(Text, nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    totals = Column(JSONB, nullable=False, default=dict)

    email_logs = relationship(
        "EmailLog",
        back_populates="campaign_run",
        cascade="all, delete-orphan",
    )