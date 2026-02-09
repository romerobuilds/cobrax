from pydantic import BaseModel, EmailStr
from uuid import UUID

class CompanyCreate(BaseModel):
    nome: str
    cnpj: str
    email: str

class CompanyOut(BaseModel):
    id: str
    nome: str
    cnpj: str
    email: str

    class Config:
        from_attributes = True  # Pydantic v2
# usado para responder ao client (saída)

class CompanyPublic(BaseModel):
    id: UUID
    nome: str
    cnpj: str
    email: str
    owner_id: UUID

    class Config:
        from_attributes = True  # SQLAlchemy → Pydantic (obrigatório)
