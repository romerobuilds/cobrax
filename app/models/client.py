#app/models/client.py
from __future__ import annotations

import uuid

from sqlalchemy import Column, String, ForeignKey, DateTime, Boolean, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database_.database import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    nome = Column(String, nullable=False)
    email = Column(String, nullable=False)
    telefone = Column(String, nullable=True)

    # ✅ cobrança/asaas
    cpf_cnpj = Column(String, nullable=True)

    # ✅ origem / integrações
    source_system = Column(String, nullable=True)          # ex: CAKTO
    source_external_ref = Column(String, nullable=True)    # ex: order_id / customer ref
    last_order_at = Column(DateTime(timezone=True), nullable=True)

    # (opcionais - ajudam muito na emissão e em outras integrações)
    endereco = Column(String, nullable=True)
    endereco_numero = Column(String, nullable=True)
    complemento = Column(String, nullable=True)
    bairro = Column(String, nullable=True)
    cidade = Column(String, nullable=True)
    estado = Column(String, nullable=True)
    cep = Column(String, nullable=True)

    # ✅ seus campos atuais
    is_mensalista = Column(Boolean, nullable=False, server_default="false")
    saldo_aberto = Column(Numeric(12, 2), nullable=False, server_default="0")

    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    owner = relationship("User", back_populates="clients")

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    company = relationship("Company", back_populates="clients")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    email_logs = relationship(
        "EmailLog",
        back_populates="client",
        cascade="all, delete-orphan",
    )