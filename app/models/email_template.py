# app/models/email_template.py
import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    nome = Column(String(120), nullable=False)
    assunto = Column(String(180), nullable=False)
    corpo_html = Column(Text, nullable=False)

    ativo = Column(Boolean, nullable=False, server_default="true")

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    company = relationship("Company", back_populates="email_templates")

    # ✅ necessário por causa do EmailLog.template(back_populates="email_logs")
    email_logs = relationship(
        "EmailLog",
        back_populates="template",
        cascade="all, delete-orphan",
    )
