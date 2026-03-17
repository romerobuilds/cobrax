from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_company_for_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.schemas.asaas_settings import AsaasSettingsOut, AsaasSettingsUpdate
from app.services.asaas_client import ping_asaas

router = APIRouter(
    prefix="/empresas/{company_id}/asaas-settings",
    tags=["Asaas"],
    dependencies=[Depends(get_company_for_current_user)],
)


def _get_company_or_404(db: Session, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _asaas_out(company: Company) -> AsaasSettingsOut:
    base_url = (getattr(company, "asaas_base_url", None) or "").strip() or "https://api.asaas.com/v3"
    api_key_configured = bool((getattr(company, "asaas_api_key", None) or "").strip())

    return AsaasSettingsOut(
        company_id=str(company.id),
        company_name=company.nome,
        asaas_base_url=base_url,
        api_key_configured=api_key_configured,
        asaas_configured=api_key_configured,
        dashboard_url="https://www.asaas.com/",
        sandbox_url="https://sandbox.asaas.com/",
    )


@router.get("/", response_model=AsaasSettingsOut, status_code=status.HTTP_200_OK)
def get_asaas_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)
    return _asaas_out(company)


@router.put("/", response_model=AsaasSettingsOut, status_code=status.HTTP_200_OK)
def put_asaas_settings(
    company_id: UUID,
    payload: AsaasSettingsUpdate,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    if payload.asaas_base_url is not None:
        company.asaas_base_url = payload.asaas_base_url.strip() or None

    if payload.asaas_api_key is not None:
        new_key = payload.asaas_api_key.strip()
        if new_key:
            company.asaas_api_key = new_key

    db.add(company)
    db.commit()
    db.refresh(company)

    return _asaas_out(company)


@router.post("/test", status_code=status.HTTP_200_OK)
def test_asaas_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    api_key = (company.asaas_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Configure a API Key do Asaas antes do teste")

    try:
        ping_asaas(
            api_key=api_key,
            base_url=company.asaas_base_url,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Não foi possível validar o Asaas: {e}")

    return {
        "ok": True,
        "message": "Integração Asaas validada com sucesso",
    }