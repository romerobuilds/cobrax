from pydantic import BaseModel, EmailStr, Field

class UserCreate(BaseModel):
    email: EmailStr
    nome: str
    senha: str = Field(min_length=6, max_length=72)

class UserPublic(BaseModel):
    id: str
    email: EmailStr
    nome: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
