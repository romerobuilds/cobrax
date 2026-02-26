from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ClientCreate(BaseModel):
    nome: str
    email: EmailStr
    telefone: Optional[str] = None

    # ✅ novos campos (opcionais no create)
    is_mensalista: Optional[bool] = False
    saldo_aberto: Optional[Decimal] = Decimal("0.00")


class ClientUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[EmailStr] = None
    telefone: Optional[str] = None

    # ✅ novos campos
    is_mensalista: Optional[bool] = None
    saldo_aberto: Optional[Decimal] = None


class ClientPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    owner_id: UUID
    nome: str
    email: EmailStr
    telefone: Optional[str] = None
    created_at: datetime

    # ✅ novos campos no response
    is_mensalista: bool
    saldo_aberto: Decimal


class ClientUploadResult(BaseModel):
    ok: bool = True
    added: int = 0
    updated: int = 0
    skipped_no_email: int = 0
    skipped_invalid: int = 0
    errors: List[str] = Field(default_factory=list)
    note: str = (
        "Envie CSV/XLSX com colunas: email, nome, telefone, mensalista, saldo_aberto. "
        "Se update_existing=true, atualiza cliente existente por email."
    )