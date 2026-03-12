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
    """
    Master:
      - vê empresas que possui
      - ou, se for master de suporte, vê empresas onde foi vinculado com role master

    Company user:
      - vê apenas empresas onde possui membership ativo
    """
    if user.is_master:
        owned_companies = (
            db.query(Company)
            .filter(Company.owner_id == user.id)
            .order_by(Company.nome.asc())
            .all()
        )

        membership_rows = (
            db.query(CompanyUser, Company)
            .join(Company, Company.id == CompanyUser.company_id)
            .filter(
                CompanyUser.user_id == user.id,
                CompanyUser.is_active.is_(True),
            )
            .order_by(Company.nome.asc())
            .all()
        )

        merged: dict[str, AccessibleCompany] = {}

        for c in owned_companies:
            merged[str(c.id)] = AccessibleCompany(
                id=str(c.id),
                nome=c.nome,
                email=c.email,
                role="master",
            )

        for membership, company in membership_rows:
            merged[str(company.id)] = AccessibleCompany(
                id=str(company.id),
                nome=company.nome,
                email=company.email,
                role=membership.role or "master",
            )

        return sorted(merged.values(), key=lambda x: (x.nome or "").lower())

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

    cobrax = (
        db.query(Company)
        .filter(Company.nome.ilike("cobrax"))
        .order_by(Company.nome.asc())
        .first()
    )
    if cobrax and str(cobrax.id) in company_ids:
        return str(cobrax.id)

    return companies[0].id if companies else None


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

    allowed_ids = {c.id for c in _build_accessible_companies(db, user)}
    if str(payload.company_id) not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")

    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

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
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if user.is_master:
        allowed_ids = {c.id for c in _build_accessible_companies(db, user)}
        if str(company_id) not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")
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
                "is_master": bool(user_obj.is_master),
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
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    accessible = _build_accessible_companies(db, user)
    accessible_ids = {c.id for c in accessible}

    if str(company_id) not in accessible_ids and not user.is_master:
        raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")

    if not user.is_master:
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

    creator_home_company_id = _resolve_master_home_company_id(db, user, accessible) if user.is_master else None
    is_creating_from_master_base = bool(
        user.is_master
        and creator_home_company_id
        and str(company_id) == str(creator_home_company_id)
    )

    new_user = User(
        nome=payload.nome.strip(),
        email=str(payload.email).strip().lower(),
        senha_hash=hash_senha(payload.senha),
        is_master=bool(is_creating_from_master_base),
        home_company_id=UUID(str(creator_home_company_id)) if is_creating_from_master_base else None,
    )
    db.add(new_user)
    db.flush()

    if is_creating_from_master_base:
        # Usuário nasce master e já recebe acesso a todas as empresas do ecossistema do master atual
        master_companies = (
            db.query(Company)
            .join(CompanyUser, CompanyUser.company_id == Company.id, isouter=True)
            .filter(
                (Company.owner_id == user.id) |
                (Company.id.in_([UUID(c.id) for c in accessible if c.id]))
            )
            .distinct()
            .order_by(Company.nome.asc())
            .all()
        )

        if not master_companies:
            master_companies = [company]

        for target_company in master_companies:
            db.add(
                CompanyUser(
                    company_id=target_company.id,
                    user_id=new_user.id,
                    role="master",
                    is_active=True,
                )
            )
    else:
        db.add(
            CompanyUser(
                company_id=company_id,
                user_id=new_user.id,
                role=(payload.role or "company_admin").strip() or "company_admin",
                is_active=True,
            )
        )

    db.commit()
    db.refresh(new_user)

    return {
        "message": "Usuário criado com sucesso",
        "usuario": {
            "id": str(new_user.id),
            "nome": new_user.nome,
            "email": new_user.email,
            "company_id": str(company_id),
            "role": "master" if new_user.is_master else ((payload.role or "company_admin").strip() or "company_admin"),
            "is_active": True,
            "is_master": bool(new_user.is_master),
            "home_company_id": str(new_user.home_company_id) if new_user.home_company_id else None,
        },
    }