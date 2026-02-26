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

    # NOVOS CAMPOS
    is_mensalista: bool = False
    saldo_aberto: Decimal = Field(default=Decimal("0.00"))


class ClientUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[EmailStr] = None
    telefone: Optional[str] = None

    # NOVOS CAMPOS
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

    # NOVOS CAMPOS
    is_mensalista: bool
    saldo_aberto: Decimal

    created_at: datetime


# =========================
# UPLOAD (Fase B)
# =========================

class ClientUploadResult(BaseModel):
    ok: bool = True
    created: int = 0
    updated: int = 0
    skipped_no_email: int = 0
    skipped_duplicate: int = 0
    errors: List[str] = Field(default_factory=list)
    note: str = (
        "Envie CSV/XLSX com colunas: email, nome, telefone, mensalista, saldo_aberto "
        "(nomes podem ser configurados via query params). "
        "Se update_existing=true, atualiza cliente existente por email."
    )