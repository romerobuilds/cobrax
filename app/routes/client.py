from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.client import Client
from app.models.company import Company
from app.models.user import User
from app.schemas.client import ClientCreate, ClientPublic, ClientUpdate

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


@router.post("/", response_model=ClientPublic, status_code=status.HTTP_201_CREATED)
def criar_cliente(
    company_id: UUID,
    payload: ClientCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)

    # evita duplicado por email dentro da mesma empresa
    existe = (
        db.query(Client)
        .filter(Client.company_id == company_id, Client.email == payload.email)
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

    # se for trocar email, valida duplicidade
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
