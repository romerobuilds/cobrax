# app/routers/webhooks_asaas.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.client import Client


router = APIRouter(prefix="/webhooks/asaas", tags=["Webhooks - Asaas"])


def _expected_token() -> str:
    return (os.getenv("ASAAS_WEBHOOK_TOKEN") or "").strip()


@router.post("/")
async def asaas_webhook(
    request: Request,
    db: Session = Depends(get_db),
    asaas_access_token: Optional[str] = Header(default=None, alias="asaas-access-token"),
):
    expected = _expected_token()
    if expected:
        if not asaas_access_token or asaas_access_token.strip() != expected:
            raise HTTPException(status_code=401, detail="Webhook token inválido")

    payload: Dict[str, Any] = await request.json()
    event = (payload.get("event") or "").strip()
    payment = payload.get("payment") or {}
    payment_id = payment.get("id")

    if not payment_id:
        # não quebra a fila do Asaas
        return {"ok": True, "ignored": True, "reason": "no payment.id"}

    charge: BillingCharge | None = (
        db.query(BillingCharge).filter(BillingCharge.asaas_payment_id == str(payment_id)).first()
    )
    if not charge:
        return {"ok": True, "ignored": True, "reason": "payment not mapped"}

    # Normaliza status (MVP)
    # Eventos comuns: PAYMENT_RECEIVED / PAYMENT_CONFIRMED / PAYMENT_OVERDUE / PAYMENT_DELETED ...
    now = datetime.now(timezone.utc)

    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        charge.status = "PAID"
        charge.paid_at = now

        # regra simples: pago => zera saldo_aberto do cliente
        c = db.query(Client).filter(Client.id == charge.client_id).first()
        if c:
            c.saldo_aberto = 0

    elif event in ("PAYMENT_OVERDUE",):
        charge.status = "OVERDUE"

    elif event in ("PAYMENT_DELETED", "PAYMENT_REFUNDED"):
        charge.status = "CANCELLED"

    else:
        # guarda último status textual se quiser
        charge.status = charge.status or "PENDING"

    db.add(charge)
    db.commit()
    return {"ok": True, "event": event, "payment_id": str(payment_id)}