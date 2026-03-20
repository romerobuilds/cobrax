from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.models.company import Company
from app.models.cakto_webhook_event import CaktoWebhookEvent
from app.workers.tasks import process_cakto_webhook_event_job

router = APIRouter(
    prefix="/webhooks/cakto",
    tags=["Cakto Webhooks"],
)


def _pick(d, *keys):
    for key in keys:
        if isinstance(d, dict) and d.get(key) is not None:
            return d.get(key)
    return None


def _extract_event_name(payload: dict) -> str | None:
    event = payload.get("event")
    if isinstance(event, dict):
        return _pick(event, "custom_id", "id", "name")
    return _pick(payload, "custom_id", "event_type", "event", "type")


def _extract_external_event_id(payload: dict) -> str | None:
    event = payload.get("event")
    if isinstance(event, dict):
        val = _pick(event, "id")
        if val is not None:
            return str(val)
    val = _pick(payload, "event_id", "id")
    return str(val) if val is not None else None


def _extract_order_id(payload: dict) -> str | None:
    candidates = [
        payload,
        payload.get("data") if isinstance(payload.get("data"), dict) else {},
        payload.get("order") if isinstance(payload.get("order"), dict) else {},
        payload.get("purchase") if isinstance(payload.get("purchase"), dict) else {},
    ]

    for obj in candidates:
        val = _pick(obj, "order_id", "id", "_id")
        if val is not None:
            return str(val).strip()

    data = payload.get("data") or {}
    order = data.get("order") if isinstance(data, dict) else {}
    if isinstance(order, dict):
        val = _pick(order, "id", "_id", "order_id")
        if val is not None:
            return str(val).strip()

    return None


def _build_dedupe_key(payload: dict) -> str:
    event_name = str(_extract_event_name(payload) or "").strip()
    external_event_id = str(_extract_external_event_id(payload) or "").strip()
    order_id = str(_extract_order_id(payload) or "").strip()

    if external_event_id:
        return f"evt:{external_event_id}"

    if event_name and order_id:
        return f"{event_name}:{order_id}"

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/{webhook_token}", status_code=status.HTTP_200_OK)
async def receive_cakto_webhook(
    webhook_token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    company = (
        db.query(Company)
        .filter(Company.cakto_webhook_token == webhook_token)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Webhook não encontrado")

    if not bool(getattr(company, "cakto_enabled", False)):
        raise HTTPException(status_code=400, detail="Integração Cakto desabilitada para esta empresa")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON inválido")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload inválido")

    # verificação opcional por segredo, se a Cakto enviar esse valor
    incoming_secret = (
        request.headers.get("x-cakto-secret")
        or request.headers.get("x-webhook-secret")
        or request.headers.get("x-cakto-webhook-secret")
        or _pick(payload, "secret")
    )
    saved_secret = (getattr(company, "cakto_webhook_secret", None) or "").strip()

    if saved_secret and incoming_secret and str(incoming_secret).strip() != saved_secret:
        raise HTTPException(status_code=403, detail="Secret do webhook inválido")

    event_name = _extract_event_name(payload)
    external_event_id = _extract_external_event_id(payload)
    external_order_id = _extract_order_id(payload)
    dedupe_key = _build_dedupe_key(payload)

    existing = (
        db.query(CaktoWebhookEvent)
        .filter(
            CaktoWebhookEvent.company_id == company.id,
            CaktoWebhookEvent.dedupe_key == dedupe_key,
        )
        .first()
    )
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "message": "Evento já recebido anteriormente",
        }

    row = CaktoWebhookEvent(
        company_id=company.id,
        webhook_token=webhook_token,
        dedupe_key=dedupe_key,
        event_name=str(event_name) if event_name is not None else None,
        external_event_id=str(external_event_id) if external_event_id is not None else None,
        external_order_id=str(external_order_id) if external_order_id is not None else None,
        status="RECEIVED",
        payload=payload,
    )
    db.add(row)

    company.cakto_last_webhook_at = datetime.now(timezone.utc)
    db.add(company)

    db.commit()
    db.refresh(row)

    process_cakto_webhook_event_job.delay(str(row.id))

    return {
        "ok": True,
        "queued": True,
        "message": "Evento recebido com sucesso",
    }