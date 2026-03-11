from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserCreate(BaseModel):
    email: EmailStr
    nome: str
    senha: str = Field(min_length=6, max_length=72)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str
    is_master: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AccessibleCompany(BaseModel):
    id: str
    nome: str
    email: str | None = None
    role: str


class MeResponse(BaseModel):
    id: str
    email: str
    nome: str
    is_master: bool
    profile_type: str
    locked_company_id: str | None = None
    accessible_companies: list[AccessibleCompany] = []