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

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
