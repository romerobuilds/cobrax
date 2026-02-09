import uuid
from sqlalchemy.dialects.postgresql import UUID  # ✅ ESTE UUID aceita as_uuid=True
from sqlalchemy import Column, String
from app.database_.database import Base
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    nome = Column(String, nullable=False)
    senha_hash = Column(String, nullable=False)
    companies = relationship("Company", back_populates="owner", cascade="all, delete-orphan")
    clients = relationship("Client", back_populates="owner", cascade="all, delete-orphan")
