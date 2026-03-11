from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import hash_senha
from app.database_.database import get_db
from app.models.company import Company
from app.models.company_user import CompanyUser
from app.models.user import User
from app.schemas.auth import AccessibleCompany, MeResponse

router = APIRouter(tags=["Usuários"])


class CompanyUserCreateIn(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    email: EmailStr
    senha: str = Field(min_length=6, max_length=72)
    role: str = "company_admin"


def _get_company_owned_by_master_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user.id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _build_accessible_companies(db: Session, user: User) -> list[AccessibleCompany]:
    if user.is_master:
        companies = (
            db.query(Company)
            .filter(Company.owner_id == user.id)
            .order_by(Company.nome.asc())
            .all()
        )

        return [
            AccessibleCompany(
                id=str(c.id),
                nome=c.nome,
                email=c.email,
                role="master",
            )
            for c in companies
        ]

    memberships = (
        db.query(CompanyUser, Company)
        .join(Company, Company.id == CompanyUser.company_id)
        .filter(
            CompanyUser.user_id == user.id,
            CompanyUser.is_active.is_(True),
        )
        .order_by(Company.nome.asc())
        .all()
    )

    return [
        AccessibleCompany(
            id=str(company.id),
            nome=company.nome,
            email=company.email,
            role=membership.role,
        )
        for membership, company in memberships
    ]


@router.get("/me", response_model=MeResponse, tags=["Auth"])
def me(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    companies = _build_accessible_companies(db, user)

    profile_type = "master" if user.is_master else "company"
    locked_company_id = None if user.is_master else (companies[0].id if companies else None)

    return MeResponse(
        id=str(user.id),
        email=user.email,
        nome=user.nome,
        is_master=bool(user.is_master),
        profile_type=profile_type,
        locked_company_id=locked_company_id,
        accessible_companies=companies,
    )


@router.get(
    "/empresas/{company_id}/usuarios",
    status_code=status.HTTP_200_OK,
)
def list_company_users(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_master:
        raise HTTPException(status_code=403, detail="Apenas usuários master podem listar usuários")

    _get_company_owned_by_master_or_404(db, company_id, user)

    rows = (
        db.query(CompanyUser, User)
        .join(User, User.id == CompanyUser.user_id)
        .filter(CompanyUser.company_id == company_id)
        .order_by(User.nome.asc())
        .all()
    )

    return {
        "items": [
            {
                "membership_id": str(membership.id),
                "user_id": str(user_obj.id),
                "nome": user_obj.nome,
                "email": user_obj.email,
                "role": membership.role,
                "is_active": bool(membership.is_active),
                "created_at": membership.created_at.isoformat() if membership.created_at else None,
            }
            for membership, user_obj in rows
        ]
    }


@router.post(
    "/empresas/{company_id}/usuarios",
    status_code=status.HTTP_201_CREATED,
)
def create_company_user(
    company_id: UUID,
    payload: CompanyUserCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_master:
        raise HTTPException(status_code=403, detail="Apenas usuários master podem criar usuários")

    _get_company_owned_by_master_or_404(db, company_id, user)

    existing_user = db.query(User).filter(User.email == payload.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Já existe um usuário com este e-mail")

    new_user = User(
        nome=payload.nome.strip(),
        email=str(payload.email).strip().lower(),
        senha_hash=hash_senha(payload.senha),
        is_master=False,
    )
    db.add(new_user)
    db.flush()

    membership = CompanyUser(
        company_id=company_id,
        user_id=new_user.id,
        role=(payload.role or "company_admin").strip() or "company_admin",
        is_active=True,
    )
    db.add(membership)
    db.commit()
    db.refresh(new_user)
    db.refresh(membership)

    return {
        "message": "Usuário criado com sucesso",
        "usuario": {
            "membership_id": str(membership.id),
            "id": str(new_user.id),
            "nome": new_user.nome,
            "email": new_user.email,
            "company_id": str(company_id),
            "role": membership.role,
            "is_active": bool(membership.is_active),
        },
    }