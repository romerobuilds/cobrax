from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class CompanyCreate(BaseModel):
    nome: str
    cnpj: str
    email: EmailStr

    initial_user_nome: str = Field(min_length=2, max_length=120)
    initial_user_email: EmailStr
    initial_user_senha: str = Field(min_length=6, max_length=72)


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