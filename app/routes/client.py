# app/routes/client.py
from __future__ import annotations

import csv
import io
from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.client import Client
from app.models.company import Company
from app.models.user import User
from app.schemas.client import ClientCreate, ClientPublic, ClientUpdate

# XLSX
from openpyxl import load_workbook


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


# -------------------------
# Upload helpers
# -------------------------

def _normalize_header(s: str) -> str:
    return (s or "").strip()


def _parse_csv(raw: bytes, limit: int) -> List[Dict[str, str]]:
    text = raw.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    rows: List[Dict[str, str]] = []
    for i, row in enumerate(reader):
        if i >= limit:
            break
        if not row:
            continue
        clean: Dict[str, str] = {}
        for k, v in row.items():
            hk = _normalize_header(k)
            if not hk:
                continue
            clean[hk] = "" if v is None else str(v).strip()
        if clean:
            rows.append(clean)
    return rows


def _parse_xlsx(raw: bytes, limit: int) -> List[Dict[str, str]]:
    wb = load_workbook(filename=io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        headers = next(rows_iter)
    except StopIteration:
        return []

    headers_norm = [_normalize_header(str(h) if h is not None else "") for h in headers]
    rows: List[Dict[str, str]] = []

    for i, values in enumerate(rows_iter):
        if i >= limit:
            break
        row: Dict[str, str] = {}
        for idx, h in enumerate(headers_norm):
            if not h:
                continue
            v = values[idx] if idx < len(values) else None
            row[h] = "" if v is None else str(v).strip()
        if row:
            rows.append(row)

    return rows


def _parse_upload_file(file: UploadFile, raw: bytes, limit: int) -> List[Dict[str, str]]:
    filename = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()

    if filename.endswith(".csv") or "csv" in ctype:
        return _parse_csv(raw, limit)

    if filename.endswith(".xlsx") or "spreadsheet" in ctype or "excel" in ctype:
        return _parse_xlsx(raw, limit)

    raise HTTPException(status_code=400, detail="Formato inválido. Envie CSV ou XLSX.")


def _find_value_ci(row: Dict[str, Any], col: str) -> Optional[str]:
    """Busca a coluna por match case-insensitive."""
    col = (col or "").strip()
    if not col:
        return None

    if col in row:
        return row.get(col)

    target = col.lower()
    for k in row.keys():
        if (k or "").strip().lower() == target:
            return row.get(k)
    return None


# -------------------------
# CRUD
# -------------------------

@router.post("/", response_model=ClientPublic, status_code=status.HTTP_201_CREATED)
def criar_cliente(
    company_id: UUID,
    payload: ClientCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)

    existe = (
        db.query(Client)
        .filter(Client.company_id == company_id, Client.email == str(payload.email))
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


@router.get("/", response_model=List[ClientPublic])
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


@router.get("/{client_id}", response_model=ClientPublic)
def obter_cliente(
    company_id: UUID,
    client_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)
    return _get_client_or_404(db, company_id, client_id, user.id)


@router.put("/{client_id}", response_model=ClientPublic)
def atualizar_cliente(
    company_id: UUID,
    client_id: UUID,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)
    client = _get_client_or_404(db, company_id, client_id, user.id)

    if payload.email and str(payload.email) != client.email:
        existe = (
            db.query(Client)
            .filter(
                Client.company_id == company_id,
                Client.email == str(payload.email),
                Client.id != client.id,
            )
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


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
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
# UPLOAD (CSV/XLSX)
# =========================

@router.post("/upload", operation_id="clients_upload")
async def upload_clients_file(
    company_id: UUID,
    file: UploadFile = File(...),
    email_column: str = Query(default="email", description="Nome da coluna do e-mail (ex: email)"),
    nome_column: str = Query(default="nome", description="Nome da coluna do nome (ex: nome)"),
    telefone_column: str = Query(default="telefone", description="Nome da coluna do telefone (ex: telefone)"),
    limit: int = Query(default=5000, ge=1, le=50000, description="Máximo de linhas lidas"),
    update_existing: bool = Query(default=False, description="Se true, atualiza cliente existente pelo e-mail"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload CSV/XLSX de clientes:
    - email_column: obrigatório por linha
    - nome/telefone opcionais (se não vierem, deixa como está)
    - dedupe por email dentro da empresa
    - update_existing=true => atualiza nome/telefone do cliente existente
    """
    _get_company_or_404(db, company_id, user.id)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    rows = _parse_upload_file(file, raw, limit)
    if not rows:
        raise HTTPException(status_code=400, detail="Nenhuma linha válida encontrada")

    email_col = (email_column or "email").strip()
    nome_col = (nome_column or "nome").strip()
    tel_col = (telefone_column or "telefone").strip()

    # cache de emails existentes
    existing = {
        (e or "").lower(): cid
        for (e, cid) in db.query(Client.email, Client.id)
        .filter(Client.company_id == company_id, Client.owner_id == user.id)
        .all()
        if e
    }

    created = 0
    updated = 0
    skipped_no_email = 0
    skipped_duplicate = 0
    errors: List[str] = []

    for idx, row in enumerate(rows, start=1):
        email_val = _find_value_ci(row, email_col)
        email = (email_val or "").strip()
        if not email:
            skipped_no_email += 1
            continue

        email_key = email.lower()

        nome_val = _find_value_ci(row, nome_col)
        telefone_val = _find_value_ci(row, tel_col)

        nome = (nome_val or "").strip()
        telefone = (telefone_val or "").strip()

        if email_key in existing:
            if not update_existing:
                skipped_duplicate += 1
                continue

            # update existente
            client_id = existing[email_key]
            c = (
                db.query(Client)
                .filter(
                    Client.id == client_id,
                    Client.company_id == company_id,
                    Client.owner_id == user.id,
                )
                .first()
            )
            if not c:
                skipped_duplicate += 1
                continue

            if nome:
                c.nome = nome
            if telefone:
                c.telefone = telefone

            updated += 1
            continue

        # create novo
        if not nome:
            # se planilha não tiver nome, cria um fallback
            nome = email.split("@")[0]

        c = Client(
            nome=nome,
            email=email,
            telefone=telefone or None,
            owner_id=user.id,
            company_id=company_id,
        )

        db.add(c)
        try:
            db.flush()  # pega erros antes do commit
            existing[email_key] = c.id
            created += 1
        except IntegrityError:
            db.rollback()
            skipped_duplicate += 1
        except Exception as e:
            db.rollback()
            errors.append(f"linha {idx}: {str(e)}")

    db.commit()

    return {
        "ok": True,
        "created": int(created),
        "updated": int(updated),
        "skipped_no_email": int(skipped_no_email),
        "skipped_duplicate": int(skipped_duplicate),
        "errors": errors[:20],
        "note": "CSV/XLSX: email é obrigatório. update_existing=true atualiza nome/telefone do cliente existente pelo e-mail.",
    }