# app/routers/webhook_asaas.py
from __future__ import annotations

import os
import re
from datetime import datetime, timezone, date
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.client import Client

router = APIRouter(prefix="/webhook/asaas", tags=["Webhooks - Asaas"])


def _expected_token() -> str:
    return (os.getenv("ASAAS_WEBHOOK_TOKEN") or "").strip()


def _parse_date(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None


def _is_uuid(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            text.strip(),
        )
    )


def _try_parse_external_reference(ext: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not ext:
        return (None, None)

    m_company = re.search(r"company:([0-9a-fA-F-]{36})", ext)
    m_client = re.search(r"client:([0-9a-fA-F-]{36})", ext)

    return (m_company.group(1) if m_company else None, m_client.group(1) if m_client else None)


@router.get("")
def ping():
    return {"ok": True, "service": "asaas-webhook"}


@router.post("")
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
        return {"ok": True, "ignored": True, "reason": "no payment.id"}

    status = payment.get("status") or "PENDING"
    value = payment.get("value") or 0
    due_date = _parse_date(payment.get("dueDate"))
    invoice_url = payment.get("invoiceUrl")
    bank_slip_url = payment.get("bankSlipUrl")
    external_reference = (payment.get("externalReference") or "").strip() or None

    now = datetime.now(timezone.utc)

    charge: BillingCharge | None = (
        db.query(BillingCharge)
        .filter(BillingCharge.asaas_payment_id == str(payment_id))
        .first()
    )

    if not charge and external_reference and _is_uuid(external_reference):
        maybe_charge = db.query(BillingCharge).filter(BillingCharge.id == external_reference).first()
        if maybe_charge:
            maybe_charge.asaas_payment_id = str(payment_id)
            maybe_charge.status = str(status)
            maybe_charge.value = value
            maybe_charge.due_date = due_date
            maybe_charge.invoice_url = invoice_url
            maybe_charge.bank_slip_url = bank_slip_url
            maybe_charge.updated_at = now

            db.add(maybe_charge)
            db.commit()
            db.refresh(maybe_charge)
            charge = maybe_charge

    if not charge and external_reference:
        company_id, client_id = _try_parse_external_reference(external_reference)
        if client_id and company_id:
            charge = (
                db.query(BillingCharge)
                .filter(
                    BillingCharge.company_id == company_id,
                    BillingCharge.client_id == client_id,
                    BillingCharge.status.in_(["PENDING", "RECEIVED", "CONFIRMED", "OVERDUE"]),
                )
                .order_by(BillingCharge.created_at.desc())
                .first()
            )
            if charge:
                charge.asaas_payment_id = str(payment_id)
                charge.status = str(status)
                charge.value = value
                charge.due_date = due_date
                charge.invoice_url = invoice_url
                charge.bank_slip_url = bank_slip_url
                charge.updated_at = now
                db.add(charge)
                db.commit()
                db.refresh(charge)

    if not charge:
        return {
            "ok": True,
            "ignored": True,
            "reason": "payment not mapped",
            "event": event,
            "payment_id": str(payment_id),
            "externalReference": external_reference,
        }

    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        charge.status = "PAID"
        charge.paid_at = now

        c = db.query(Client).filter(Client.id == charge.client_id).first()
        if c:
            c.saldo_aberto = 0

    elif event in ("PAYMENT_OVERDUE",):
        charge.status = "OVERDUE"

    elif event in ("PAYMENT_DELETED", "PAYMENT_REFUNDED"):
        charge.status = "CANCELLED"

    else:
        charge.status = str(status)

    charge.value = value
    charge.due_date = due_date
    charge.invoice_url = invoice_url
    charge.bank_slip_url = bank_slip_url
    charge.updated_at = now

    db.add(charge)
    db.commit()

    return {
        "ok": True,
        "event": event,
        "payment_id": str(payment_id),
        "charge_id": str(charge.id),
    }