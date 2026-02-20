# app/schemas/client.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ClientCreate(BaseModel):
    nome: str
    email: EmailStr
    telefone: str


class ClientUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[EmailStr] = None
    telefone: Optional[str] = None


class ClientPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    owner_id: UUID
    nome: str
    email: EmailStr
    telefone: str
    created_at: datetime


# =========================
# UPLOAD (Fase B)
# =========================

class ClientUploadResult(BaseModel):
    ok: bool = True
    added: int = 0
    updated: int = 0
    skipped_no_email: int = 0
    skipped_invalid: int = 0
    errors: List[str] = Field(default_factory=list)
    note: str = (
        "Envie CSV/XLSX com colunas: email, nome, telefone (ou as que existirem). "
        "Se upsert=true, atualiza cliente existente por email."
    )