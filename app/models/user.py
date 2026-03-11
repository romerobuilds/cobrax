import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    nome = Column(String, nullable=False)
    senha_hash = Column(String, nullable=False)

    # 🔐 Todos os usuários atuais continuam master por padrão,
    # para não quebrar o sistema atual.
    is_master = Column(Boolean, nullable=False, default=True)

    companies = relationship(
        "Company",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    clients = relationship(
        "Client",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    company_memberships = relationship(
        "CompanyUser",
        back_populates="user",
        cascade="all, delete-orphan",
    )