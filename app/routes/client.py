# app/routes/client.py
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.client import Client
from app.models.company import Company
from app.models.user import User
from app.schemas.client import ClientCreate, ClientPublic, ClientUpdate

from app.services.upload_parser import parse_upload_file

router = APIRouter(prefix="/empresas/{company_id}/clientes", tags=["Clientes"])


def _get_company_or_404(db: Session, company_id: UUID, user_id: UUID) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user_id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada ou não pertence a você")
    return company


def _get_client_or_404(db: Session, company_id: UUID, client_id: UUID, user_id: UUID) -> Client:
    client = (
        db.query(Client)
        .filter(
            Client.id == client_id,
            Client.company_id == company_id,
            Client.owner_id == user_id,
        )
        .first()
    )
    if not client:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return client


def _get_ci(row: dict, colname: str) -> Optional[str]:
    """Busca valor por coluna case-insensitive."""
    if not colname:
        return None
    if colname in row:
        return row.get(colname)
    target = colname.strip().lower()
    for k in row.keys():
        if (k or "").strip().lower() == target:
            return row.get(k)
    return None


# =========================
# CRUD
# =========================

@router.post("/", response_model=ClientPublic, status_code=status.HTTP_201_CREATED, operation_id="clients_create")
def criar_cliente(
    company_id: UUID,
    payload: ClientCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)

    # evita duplicado por email dentro da mesma empresa (case-insensitive)
    existe = (
        db.query(Client)
        .filter(Client.company_id == company_id)
        .filter(func.lower(Client.email) == func.lower(str(payload.email)))
        .first()
    )
    if existe:
        raise HTTPException(status_code=400, detail="Já existe cliente com esse e-mail nesta empresa")

    client = Client(
        nome=payload.nome,
        email=str(payload.email),
        telefone=payload.telefone,
        owner_id=user.id,
        company_id=company_id,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


@router.get("/", response_model=List[ClientPublic], operation_id="clients_list")
def listar_clientes(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)

    return (
        db.query(Client)
        .filter(Client.company_id == company_id, Client.owner_id == user.id)
        .order_by(Client.created_at.desc())
        .all()
    )


@router.get("/{client_id}", response_model=ClientPublic, operation_id="clients_get")
def obter_cliente(
    company_id: UUID,
    client_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)
    return _get_client_or_404(db, company_id, client_id, user.id)


@router.put("/{client_id}", response_model=ClientPublic, operation_id="clients_update")
def atualizar_cliente(
    company_id: UUID,
    client_id: UUID,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)
    client = _get_client_or_404(db, company_id, client_id, user.id)

    # se for trocar email, valida duplicidade (case-insensitive)
    if payload.email and str(payload.email) != client.email:
        existe = (
            db.query(Client)
            .filter(Client.company_id == company_id)
            .filter(func.lower(Client.email) == func.lower(str(payload.email)))
            .filter(Client.id != client.id)
            .first()
        )
        if existe:
            raise HTTPException(status_code=400, detail="Já existe cliente com esse e-mail nesta empresa")

    if payload.nome is not None:
        client.nome = payload.nome
    if payload.email is not None:
        client.email = str(payload.email)
    if payload.telefone is not None:
        client.telefone = payload.telefone

    db.commit()
    db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="clients_delete")
def deletar_cliente(
    company_id: UUID,
    client_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)
    client = _get_client_or_404(db, company_id, client_id, user.id)

    db.delete(client)
    db.commit()
    return None


# =========================
# UPLOAD (Fase B)
# =========================

@router.post("/upload", operation_id="clients_upload")
async def upload_clientes(
    company_id: UUID,
    file: UploadFile = File(...),
    email_column: str = Query(default="email", description="Nome da coluna do e-mail (ex: email)"),
    name_column: str = Query(default="nome", description="Nome da coluna do nome (ex: nome)"),
    phone_column: str = Query(default="telefone", description="Nome da coluna do telefone (ex: telefone)"),
    limit: int = Query(default=5000, ge=1, le=50000, description="Máximo de linhas lidas"),
    upsert: bool = Query(default=True, description="Se true, atualiza cliente existente por email"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload CSV/XLSX para criar/atualizar clientes em massa.
    - Identificador: email (case-insensitive) dentro da empresa
    - upsert=true: atualiza nome/telefone se vierem preenchidos
    """
    _get_company_or_404(db, company_id, user.id)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    try:
        rows = parse_upload_file(file.filename or "", raw, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="Nenhuma linha válida encontrada")

    email_col = (email_column or "email").strip()
    name_col = (name_column or "nome").strip()
    phone_col = (phone_column or "telefone").strip()

    added = 0
    updated = 0
    skipped_no_email = 0
    skipped_invalid = 0
    skipped_duplicate_when_no_upsert = 0
    errors: List[str] = []

    for idx, row in enumerate(rows, start=1):
        email_val = _get_ci(row, email_col)
        email_str = (email_val or "").strip()

        if not email_str:
            skipped_no_email += 1
            continue

        nome_val = _get_ci(row, name_col)
        tel_val = _get_ci(row, phone_col)

        nome_str = (nome_val or "").strip()
        tel_str = (tel_val or "").strip()

        try:
            existing = (
                db.query(Client)
                .filter(Client.company_id == company_id, Client.owner_id == user.id)
                .filter(func.lower(Client.email) == func.lower(email_str))
                .first()
            )

            if existing:
                if not upsert:
                    skipped_duplicate_when_no_upsert += 1
                    continue

                # só atualiza se vier valor (não sobrescreve com vazio)
                if nome_str:
                    existing.nome = nome_str
                if tel_str:
                    existing.telefone = tel_str

                updated += 1
            else:
                # Se seu model exigir nome/telefone NOT NULL, usamos fallback seguro
                c = Client(
                    company_id=company_id,
                    owner_id=user.id,
                    nome=nome_str or "Sem nome",
                    email=email_str,
                    telefone=tel_str or "-",
                )
                db.add(c)
                added += 1

            if (idx % 200) == 0:
                db.flush()

        except Exception as e:
            db.rollback()
            skipped_invalid += 1
            errors.append(f"linha {idx}: {str(e)}")

    db.commit()

    return {
        "ok": True,
        "added": int(added),
        "updated": int(updated),
        "skipped_no_email": int(skipped_no_email),
        "skipped_duplicate_when_no_upsert": int(skipped_duplicate_when_no_upsert),
        "skipped_invalid": int(skipped_invalid),
        "errors": errors[:20],
        "note": "Envie colunas: email, nome, telefone. upsert=true atualiza por email (case-insensitive).",
    }