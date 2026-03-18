from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_company_for_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.schemas.cakto_settings import CaktoSettingsOut, CaktoSettingsUpdate
from app.services.cakto_client import test_credentials

router = APIRouter(
    prefix="/empresas/{company_id}/cakto-settings",
    tags=["Cakto"],
    dependencies=[Depends(get_company_for_current_user)],
)


def _get_company_or_404(db: Session, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _to_iso(dt) -> str | None:
    if not dt:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return None


def _cakto_out(company: Company) -> CaktoSettingsOut:
    client_id_configured = bool((getattr(company, "cakto_client_id", None) or "").strip())
    client_secret_configured = bool((getattr(company, "cakto_client_secret", None) or "").strip())
    configured = client_id_configured and client_secret_configured

    return CaktoSettingsOut(
        company_id=str(company.id),
        company_name=company.nome,
        cakto_enabled=bool(getattr(company, "cakto_enabled", False)),
        client_id_configured=client_id_configured,
        client_secret_configured=client_secret_configured,
        cakto_configured=configured,
        cakto_connected_at=_to_iso(getattr(company, "cakto_connected_at", None)),
        cakto_last_sync_at=_to_iso(getattr(company, "cakto_last_sync_at", None)),
        api_base_url="https://api.cakto.com.br",
        token_url="https://api.cakto.com.br/public_api/token/",
    )


@router.get("/", response_model=CaktoSettingsOut, status_code=status.HTTP_200_OK)
def get_cakto_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)
    return _cakto_out(company)


@router.put("/", response_model=CaktoSettingsOut, status_code=status.HTTP_200_OK)
def put_cakto_settings(
    company_id: UUID,
    payload: CaktoSettingsUpdate,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    if payload.cakto_client_id is not None:
        company.cakto_client_id = payload.cakto_client_id.strip() or None

    if payload.cakto_client_secret is not None:
        new_secret = payload.cakto_client_secret.strip()
        if new_secret:
            company.cakto_client_secret = new_secret

    if payload.cakto_enabled is not None:
        company.cakto_enabled = bool(payload.cakto_enabled)

    has_id = bool((company.cakto_client_id or "").strip())
    has_secret = bool((company.cakto_client_secret or "").strip())
    if not (has_id and has_secret):
        company.cakto_enabled = False

    db.add(company)
    db.commit()
    db.refresh(company)

    return _cakto_out(company)


@router.post("/test", status_code=status.HTTP_200_OK)
def test_cakto_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    client_id = (company.cakto_client_id or "").strip()
    client_secret = (company.cakto_client_secret or "").strip()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Configure o Client ID e o Client Secret da Cakto antes do teste",
        )

    try:
        result = test_credentials(client_id=client_id, client_secret=client_secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Não foi possível validar a Cakto: {e}")

    company.cakto_connected_at = datetime.now(timezone.utc)
    company.cakto_enabled = True

    db.add(company)
    db.commit()
    db.refresh(company)

    return {
        "ok": True,
        "message": "Integração Cakto validada com sucesso",
        "token_type": result.get("token_type"),
        "expires_in": result.get("expires_in"),
        "scope": result.get("scope"),
    }