from pydantic import BaseModel, EmailStr
from uuid import UUID


class CompanyCreate(BaseModel):
    nome: str
    cnpj: str
    email: str

    initial_user_nome: str
    initial_user_email: EmailStr
    initial_user_senha: str


class CompanyOut(BaseModel):
    id: str
    nome: str
    cnpj: str
    email: str

    class Config:
        from_attributes = True


class CompanyPublic(BaseModel):
    id: UUID
    nome: str
    cnpj: str
    email: str
    owner_id: UUID

    class Config:
        from_attributes = True