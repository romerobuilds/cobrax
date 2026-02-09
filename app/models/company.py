# app/models/company.py
import uuid
from sqlalchemy import Column, String, ForeignKey, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database_.database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    nome = Column(String, nullable=False)
    cnpj = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False, unique=True)

    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # 🔗 Relacionamentos (strings pra evitar circular import)
    owner = relationship("User", back_populates="companies")

    # =========================
    # SMTP CONFIG
    # =========================
    smtp_host = Column(String, nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_user = Column(String, nullable=True)
    smtp_password = Column(String, nullable=True)
    smtp_use_tls = Column(Boolean, nullable=False, default=True)

    from_email = Column(String, nullable=True)
    from_name = Column(String, nullable=True)

    # =========================
    # CONTROLE DE ENVIO (PRO)
    # =========================
    smtp_paused = Column(Boolean, nullable=False, default=False)

    rate_per_min = Column(Integer, nullable=False, default=20)
    # Ex: 5, 10, 15, 20, 25, 30

    daily_email_limit = Column(Integer, nullable=True)
    # Ex: 500 (None = ilimitado)

    emails_sent_today = Column(Integer, nullable=False, default=0)
    emails_sent_today_at = Column(DateTime(timezone=True), nullable=True)

    # =========================
    # RELAÇÕES
    # =========================
    clients = relationship(
        "Client",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    email_templates = relationship(
        "EmailTemplate",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    email_logs = relationship(
        "EmailLog",
        back_populates="company",
        cascade="all, delete-orphan",
    )
