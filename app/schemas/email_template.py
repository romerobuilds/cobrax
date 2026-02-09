# app/schemas/email_template.py
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmailTemplateCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    assunto: str = Field(min_length=1, max_length=180)
    corpo_html: str = Field(min_length=1)
    ativo: bool = True


class EmailTemplateUpdate(BaseModel):
    nome: Optional[str] = Field(default=None, min_length=2, max_length=120)
    assunto: Optional[str] = Field(default=None, min_length=1, max_length=180)
    corpo_html: Optional[str] = Field(default=None, min_length=1)
    ativo: Optional[bool] = None


class EmailTemplatePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    nome: str
    assunto: str
    corpo_html: str
    ativo: bool
    created_at: datetime
    updated_at: datetime
