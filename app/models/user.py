import uuid

from sqlalchemy import Boolean, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database_.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    nome = Column(String, nullable=False)
    senha_hash = Column(String, nullable=False)

    is_master = Column(Boolean, nullable=False, default=True)

    home_company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True)

    companies = relationship(
        "Company",
        back_populates="owner",
        cascade="all, delete-orphan",
        foreign_keys="Company.owner_id",
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

    home_company = relationship(
        "Company",
        foreign_keys=[home_company_id],
        post_update=True,
    )