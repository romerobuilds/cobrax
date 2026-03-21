from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_company_for_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.schemas.cakto_settings import CaktoSettingsOut, CaktoSettingsUpdate
from app.services.cakto_client import (
    test_credentials,
    get_access_token,
    list_all_products,
    create_webhook,
    update_webhook,
    retrieve_webhook,
    test_webhook_event,
)

router = APIRouter(
    prefix="/empresas/{company_id}/cakto-settings",
    tags=["Cakto"],
    dependencies=[Depends(get_company_for_current_user)],
)

CAKTO_WEBHOOK_EVENTS = [
    "purchase_approved",
    "refund",
    "chargeback",
    "subscription_created",
    "subscription_renewed",
    "pix_gerado",
    "boleto_gerado",
    "picpay_gerado",
    "openfinance_nubank_gerado",
]


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


def _public_base_url() -> str:
    return (os.getenv("PUBLIC_APP_BASE_URL") or "https://cobrax.tech").rstrip("/")


def _ensure_webhook_token(company: Company) -> str:
    token = (company.cakto_webhook_token or "").strip()
    if token:
        return token

    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    company.cakto_webhook_token = token
    return token


def _build_webhook_url(company: Company) -> str:
    token = _ensure_webhook_token(company)
    return f"{_public_base_url()}/webhooks/cakto/{token}"


def _cakto_out(company: Company) -> CaktoSettingsOut:
    client_id_configured = bool((getattr(company, "cakto_client_id", None) or "").strip())
    client_secret_configured = bool((getattr(company, "cakto_client_secret", None) or "").strip())
    configured = client_id_configured and client_secret_configured

    webhook_configured = bool(getattr(company, "cakto_webhook_id", None) and getattr(company, "cakto_webhook_token", None))

    return CaktoSettingsOut(
        company_id=str(company.id),
        company_name=company.nome,
        cakto_enabled=bool(getattr(company, "cakto_enabled", False)),
        client_id_configured=client_id_configured,
        client_secret_configured=client_secret_configured,
        cakto_configured=configured,
        cakto_connected_at=_to_iso(getattr(company, "cakto_connected_at", None)),
        cakto_last_sync_at=_to_iso(getattr(company, "cakto_last_sync_at", None)),
        webhook_configured=webhook_configured,
        webhook_url=_build_webhook_url(company) if configured else None,
        webhook_status=getattr(company, "cakto_webhook_status", None),
        webhook_registered_at=_to_iso(getattr(company, "cakto_webhook_registered_at", None)),
        webhook_last_event_at=_to_iso(getattr(company, "cakto_last_webhook_at", None)),
        webhook_id=getattr(company, "cakto_webhook_id", None),
        api_base_url="https://api.cakto.com.br",
        token_url="https://api.cakto.com.br/public_api/token/",
    )


def _install_or_update_webhook(company: Company) -> dict:
    client_id = (company.cakto_client_id or "").strip()
    client_secret = (company.cakto_client_secret or "").strip()

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Configure a Cakto antes de registrar o webhook")

    token_data = get_access_token(client_id=client_id, client_secret=client_secret)
    access_token = str(token_data.get("access_token") or "").strip()

    products_result = list_all_products(access_token, page_size=100, max_pages=20)
    product_ids = [str((item.get("id") or "")).strip() for item in products_result["items"] if str(item.get("id") or "").strip()]

    if not product_ids:
        raise HTTPException(
            status_code=400,
            detail="Nenhum produto encontrado na Cakto para vincular ao webhook",
        )

    webhook_name = f"COBRAX • {company.nome}"
    webhook_url = _build_webhook_url(company)

    if getattr(company, "cakto_webhook_id", None):
        data = update_webhook(
            access_token,
            company.cakto_webhook_id,
            name=webhook_name,
            url=webhook_url,
            products=product_ids,
            events=CAKTO_WEBHOOK_EVENTS,
            status="active",
        )
    else:
        data = create_webhook(
            access_token,
            name=webhook_name,
            url=webhook_url,
            products=product_ids,
            events=CAKTO_WEBHOOK_EVENTS,
            status="active",
        )

    company.cakto_webhook_id = data.get("id")
    company.cakto_webhook_status = data.get("status") or "active"
    company.cakto_webhook_registered_at = datetime.now(timezone.utc)

    fields = data.get("fields") or {}
    secret = fields.get("secret")
    if secret:
        company.cakto_webhook_secret = str(secret)

    return data


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

    _ensure_webhook_token(company)

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
        _ensure_webhook_token(company)
        webhook_data = _install_or_update_webhook(company)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Não foi possível validar a Cakto: {e}")

    company.cakto_connected_at = datetime.now(timezone.utc)
    company.cakto_enabled = True

    db.add(company)
    db.commit()
    db.refresh(company)

    return {
        "ok": True,
        "message": "Integração Cakto validada e webhook configurado com sucesso",
        "token_type": result.get("token_type"),
        "expires_in": result.get("expires_in"),
        "scope": result.get("scope"),
        "webhook_id": webhook_data.get("id"),
        "webhook_status": webhook_data.get("status"),
        "webhook_url": _build_webhook_url(company),
    }


@router.post("/webhook/register", status_code=status.HTTP_200_OK)
def register_cakto_webhook(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    try:
        _ensure_webhook_token(company)
        data = _install_or_update_webhook(company)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao registrar webhook da Cakto: {e}")

    db.add(company)
    db.commit()
    db.refresh(company)

    return {
        "ok": True,
        "message": "Webhook da Cakto registrado/atualizado com sucesso",
        "webhook_id": data.get("id"),
        "webhook_status": data.get("status"),
        "webhook_url": _build_webhook_url(company),
    }


@router.post("/webhook/test", status_code=status.HTTP_200_OK)
def send_cakto_webhook_test(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    if not getattr(company, "cakto_webhook_id", None):
        raise HTTPException(status_code=400, detail="Webhook ainda não configurado para esta empresa")

    client_id = (company.cakto_client_id or "").strip()
    client_secret = (company.cakto_client_secret or "").strip()

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Configure a Cakto antes de testar o webhook")

    try:
        token_data = get_access_token(client_id=client_id, client_secret=client_secret)
        access_token = str(token_data.get("access_token") or "").strip()
        result = test_webhook_event(access_token, int(company.cakto_webhook_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao disparar teste do webhook: {e}")

    return {
        "ok": True,
        "message": "Evento de teste do webhook solicitado com sucesso",
        "result": result,
    }


@router.get("/webhook/status", status_code=status.HTTP_200_OK)
def get_cakto_webhook_status(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    if not getattr(company, "cakto_webhook_id", None):
        return {
            "ok": True,
            "configured": False,
            "webhook_id": None,
            "webhook_status": None,
            "webhook_url": _build_webhook_url(company),
        }

    client_id = (company.cakto_client_id or "").strip()
    client_secret = (company.cakto_client_secret or "").strip()

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Configure a Cakto antes de consultar o webhook")

    try:
        token_data = get_access_token(client_id=client_id, client_secret=client_secret)
        access_token = str(token_data.get("access_token") or "").strip()
        data = retrieve_webhook(access_token, int(company.cakto_webhook_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao consultar webhook da Cakto: {e}")

    fields = data.get("fields") or {}
    secret = fields.get("secret")
    if secret:
        company.cakto_webhook_secret = str(secret)

    company.cakto_webhook_status = data.get("status") or company.cakto_webhook_status
    db.add(company)
    db.commit()
    db.refresh(company)

    return {
        "ok": True,
        "configured": True,
        "webhook_id": data.get("id"),
        "webhook_status": data.get("status"),
        "webhook_url": data.get("url"),
        "events": data.get("events") or [],
        "products_count": len(data.get("products") or []),
    }