from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_company_for_current_user
from app.database_.database import get_db
from app.models.company import Company

router = APIRouter(
    prefix="/empresas/{company_id}/asaas-settings",
    tags=["Asaas"],
    dependencies=[Depends(get_company_for_current_user)],
)


class AsaasSettingsOut(BaseModel):
    company_id: str
    asaas_base_url: str | None = None
    asaas_configured: bool = False


class AsaasSettingsUpdate(BaseModel):
    asaas_api_key: str
    asaas_base_url: str | None = "https://api.asaas.com/v3"


def _get_company_or_404(db: Session, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


@router.get("/", response_model=AsaasSettingsOut, status_code=status.HTTP_200_OK)
def get_asaas_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    api_key = (getattr(company, "asaas_api_key", None) or "").strip()
    base_url = (getattr(company, "asaas_base_url", None) or "").strip() or "https://api.asaas.com/v3"

    return AsaasSettingsOut(
        company_id=str(company.id),
        asaas_base_url=base_url,
        asaas_configured=bool(api_key),
    )


@router.put("/", response_model=AsaasSettingsOut, status_code=status.HTTP_200_OK)
def put_asaas_settings(
    company_id: UUID,
    payload: AsaasSettingsUpdate,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    api_key = str(payload.asaas_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Informe a chave API do Asaas")

    base_url = str(payload.asaas_base_url or "").strip() or "https://api.asaas.com/v3"

    company.asaas_api_key = api_key
    company.asaas_base_url = base_url

    db.add(company)
    db.commit()
    db.refresh(company)

    return AsaasSettingsOut(
        company_id=str(company.id),
        asaas_base_url=company.asaas_base_url,
        asaas_configured=bool(company.asaas_api_key),
    )