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


class SetHomeCompanyIn(BaseModel):
    company_id: UUID


def _get_company_or_404(db: Session, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _resolve_master_scope_owner_id(db: Session, user: User) -> UUID | None:
    """
    Define de qual 'árvore master' o usuário faz parte.

    Regra:
    - se ele tem home_company_id, usa o owner da home company
    - senão, usa a primeira empresa onde ele é owner
    """
    if not user.is_master:
        return None

    if user.home_company_id:
        home_company = db.query(Company).filter(Company.id == user.home_company_id).first()
        if home_company:
            return home_company.owner_id

    first_owned = (
        db.query(Company)
        .filter(Company.owner_id == user.id)
        .order_by(Company.nome.asc())
        .first()
    )
    if first_owned:
        return first_owned.owner_id

    return None


def _get_company_in_master_scope_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = _get_company_or_404(db, company_id)

    owner_scope_id = _resolve_master_scope_owner_id(db, user)
    if not owner_scope_id or str(company.owner_id) != str(owner_scope_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    return company


def _build_accessible_companies(db: Session, user: User) -> list[AccessibleCompany]:
    if user.is_master:
        owner_scope_id = _resolve_master_scope_owner_id(db, user)
        if not owner_scope_id:
            return []

        companies = (
            db.query(Company)
            .filter(Company.owner_id == owner_scope_id)
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


def _resolve_master_home_company_id(
    db: Session,
    user: User,
    companies: list[AccessibleCompany],
) -> str | None:
    if not user.is_master:
        return None

    if user.home_company_id:
        return str(user.home_company_id)

    company_ids = {c.id for c in companies}
    if not company_ids:
        return None

    owner_scope_id = _resolve_master_scope_owner_id(db, user)
    if not owner_scope_id:
        return companies[0].id if companies else None

    cobrax = (
        db.query(Company)
        .filter(
            Company.owner_id == owner_scope_id,
            Company.nome.ilike("cobrax"),
        )
        .order_by(Company.nome.asc())
        .first()
    )
    if cobrax and str(cobrax.id) in company_ids:
        return str(cobrax.id)

    return companies[0].id if companies else None


def _is_master_base_company(db: Session, company_id: UUID, user: User) -> bool:
    if not user.is_master:
        return False

    home_company_id = _resolve_master_home_company_id(
        db=db,
        user=user,
        companies=_build_accessible_companies(db, user),
    )
    return bool(home_company_id and str(home_company_id) == str(company_id))


@router.get("/me", response_model=MeResponse, tags=["Auth"])
def me(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    companies = _build_accessible_companies(db, user)

    profile_type = "master" if user.is_master else "company"
    locked_company_id = None if user.is_master else (companies[0].id if companies else None)
    home_company_id = _resolve_master_home_company_id(db, user, companies)

    return MeResponse(
        id=str(user.id),
        email=user.email,
        nome=user.nome,
        is_master=bool(user.is_master),
        profile_type=profile_type,
        locked_company_id=locked_company_id,
        home_company_id=home_company_id,
        accessible_companies=companies,
    )


@router.post(
    "/me/home-company",
    status_code=status.HTTP_200_OK,
    tags=["Auth"],
)
def set_home_company(
    payload: SetHomeCompanyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_master:
        raise HTTPException(status_code=403, detail="Apenas usuários master podem definir empresa-base")

    company = _get_company_in_master_scope_or_404(db, payload.company_id, user)

    user.home_company_id = company.id
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "ok": True,
        "home_company_id": str(user.home_company_id),
        "company_name": company.nome,
    }


@router.get(
    "/empresas/{company_id}/usuarios",
    status_code=status.HTTP_200_OK,
)
def list_company_users(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id)

    if user.is_master:
        company = _get_company_in_master_scope_or_404(db, company_id, user)
    else:
        membership = (
            db.query(CompanyUser)
            .filter(
                CompanyUser.company_id == company_id,
                CompanyUser.user_id == user.id,
                CompanyUser.is_active.is_(True),
                CompanyUser.role == "company_admin",
            )
            .first()
        )
        if not membership:
            raise HTTPException(status_code=403, detail="Sem permissão para listar usuários")

    # BASE MASTER -> lista masters vinculados à base
    if _is_master_base_company(db, company_id, user):
        rows = (
            db.query(User)
            .filter(
                User.is_master.is_(True),
                User.home_company_id == company_id,
            )
            .order_by(User.nome.asc())
            .all()
        )

        return {
            "items": [
                {
                    "membership_id": None,
                    "user_id": str(u.id),
                    "nome": u.nome,
                    "email": u.email,
                    "role": "master",
                    "is_active": True,
                    "created_at": None,
                }
                for u in rows
            ]
        }

    # EMPRESA CLIENTE -> lista memberships normais
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
    company = _get_company_or_404(db, company_id)

    if user.is_master:
        company = _get_company_in_master_scope_or_404(db, company_id, user)
    else:
        membership = (
            db.query(CompanyUser)
            .filter(
                CompanyUser.company_id == company_id,
                CompanyUser.user_id == user.id,
                CompanyUser.is_active.is_(True),
                CompanyUser.role == "company_admin",
            )
            .first()
        )
        if not membership:
            raise HTTPException(status_code=403, detail="Sem permissão para criar usuários")

    existing_user = db.query(User).filter(User.email == str(payload.email).strip().lower()).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Já existe um usuário com este e-mail")

    # BASE MASTER -> cria usuário master
    if _is_master_base_company(db, company_id, user):
        new_user = User(
            nome=payload.nome.strip(),
            email=str(payload.email).strip().lower(),
            senha_hash=hash_senha(payload.senha),
            is_master=True,
            home_company_id=company_id,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return {
            "message": "Usuário master criado com sucesso",
            "usuario": {
                "membership_id": None,
                "id": str(new_user.id),
                "nome": new_user.nome,
                "email": new_user.email,
                "company_id": str(company_id),
                "role": "master",
                "is_active": True,
            },
        }

    # EMPRESA CLIENTE -> cria usuário comum da empresa
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