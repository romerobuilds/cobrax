from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.database_.database import get_db
from app.core.deps import get_current_user
from app.models.company import Company
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyPublic
from app.schemas.company_smtp_settings import CompanySmtpSettingsUpdate


router = APIRouter(prefix="/empresas", tags=["Empresas"])


@router.post("/", response_model=CompanyPublic, status_code=status.HTTP_201_CREATED)
def criar_empresa(
    payload: CompanyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # evita duplicados por cnpj/email
    existe = (
        db.query(Company)
        .filter((Company.cnpj == payload.cnpj) | (Company.email == payload.email))
        .first()
    )
    if existe:
        raise HTTPException(status_code=400, detail="Empresa já existe (cnpj/email)")

    empresa = Company(
        nome=payload.nome,
        cnpj=payload.cnpj,
        email=payload.email,
        owner_id=user.id,
    )

    db.add(empresa)
    db.commit()
    db.refresh(empresa)
    return empresa


@router.get("/me", response_model=list[CompanyPublic])
def minhas_empresas(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return db.query(Company).filter(Company.owner_id == user.id).all()


@router.patch(
    "/{company_id}/smtp-settings",
    status_code=status.HTTP_200_OK,
)
def update_smtp_settings(
    company_id: UUID,
    payload: CompanySmtpSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user.id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    # valida rate
    try:
        payload.validate_rate()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if payload.smtp_paused is not None:
        company.smtp_paused = payload.smtp_paused

    # daily limit: pode ser None (sem limite)
    if payload.daily_email_limit is not None or payload.daily_email_limit is None:
        company.daily_email_limit = payload.daily_email_limit

    if payload.rate_per_min is not None:
        company.rate_per_min = payload.rate_per_min

    db.commit()
    db.refresh(company)

    return {
        "company_id": str(company.id),
        "smtp_paused": bool(company.smtp_paused),
        "daily_email_limit": company.daily_email_limit,
        "rate_per_min": int(company.rate_per_min),
    }



