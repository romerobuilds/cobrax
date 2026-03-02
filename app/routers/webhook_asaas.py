# app/routers/webhooks_asaas.py
from __future__ import annotations

import os
import re
from datetime import datetime, timezone, date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.client import Client


router = APIRouter(prefix="/webhooks/asaas", tags=["Webhooks - Asaas"])


def _expected_token() -> str:
    return (os.getenv("ASAAS_WEBHOOK_TOKEN") or "").strip()


def _parse_due_date(due: Any) -> Optional[date]:
    """
    dueDate do Asaas costuma vir como "YYYY-MM-DD".
    """
    if not due:
        return None
    if isinstance(due, date):
        return due
    if isinstance(due, str):
        try:
            return datetime.strptime(due[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _extract_client_id_from_external_reference(external_ref: Any) -> Optional[str]:
    """
    MVP de mapeamento:
    - externalReference = "<uuid>"
    - externalReference = "client:<uuid>"
    - externalReference = "client_id=<uuid>"
    """
    if not external_ref:
        return None
    s = str(external_ref).strip()

    if _UUID_RE.match(s):
        return s

    # tenta achar uuid dentro de strings tipo "client:UUID" ou "client_id=UUID"
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", s)
    if m:
        return m.group(1)

    return None


@router.post("/", operation_id="asaas_webhook_receive")
async def asaas_webhook(
    request: Request,
    db: Session = Depends(get_db),
    asaas_access_token: Optional[str] = Header(default=None, alias="asaas-access-token"),
):
    # 1) Segurança: valida token (se você configurou)
    expected = _expected_token()
    if expected:
        if not asaas_access_token or asaas_access_token.strip() != expected:
            raise HTTPException(status_code=401, detail="Webhook token inválido")

    # 2) Lê payload
    payload: Dict[str, Any] = await request.json()
    event = (payload.get("event") or "").strip()
    payment = payload.get("payment") or {}

    payment_id = payment.get("id")
    if not payment_id:
        return {"ok": True, "ignored": True, "reason": "no payment.id"}

    asaas_payment_id = str(payment_id)

    # dados úteis do payment
    value = payment.get("value")
    status_raw = (payment.get("status") or "").strip() or None
    due_date = _parse_due_date(payment.get("dueDate"))

    invoice_url = payment.get("invoiceUrl")
    bank_slip_url = payment.get("bankSlipUrl")
    pdf_url = payment.get("pdfUrl")

    external_ref = payment.get("externalReference") or payload.get("externalReference")
    customer_id = payment.get("customer")

    now = datetime.now(timezone.utc)

    # 3) Busca cobrança existente
    charge: BillingCharge | None = (
        db.query(BillingCharge)
        .filter(BillingCharge.asaas_payment_id == asaas_payment_id)
        .first()
    )

    # 4) Se ainda não existe e o evento for criação, tenta criar
    if not charge and event == "PAYMENT_CREATED":
        # tenta mapear client_id pelo externalReference
        client_id = _extract_client_id_from_external_reference(external_ref)

        client: Client | None = None
        if client_id:
            client = db.query(Client).filter(Client.id == client_id).first()

        # Se você quiser no futuro mapear por customer_id do Asaas,
        # o certo é ter um campo tipo Client.asaas_customer_id e buscar por ele aqui.
        # Ex:
        # if not client and customer_id:
        #     client = db.query(Client).filter(Client.asaas_customer_id == str(customer_id)).first()

        if not client:
            # não quebra webhook; só ignora
            return {
                "ok": True,
                "ignored": True,
                "reason": "PAYMENT_CREATED but client not mapped (use externalReference to send client_id)",
                "asaas_payment_id": asaas_payment_id,
                "customer_id": str(customer_id) if customer_id else None,
                "externalReference": str(external_ref) if external_ref else None,
            }

        # cria cobrança
        charge = BillingCharge(
            company_id=client.company_id,
            client_id=client.id,
            asaas_payment_id=asaas_payment_id,
            status=status_raw or "PENDING",
            value=value or 0,
            due_date=due_date,
            invoice_url=invoice_url,
            bank_slip_url=bank_slip_url,
            pdf_url=pdf_url,
        )

        db.add(charge)
        db.commit()
        db.refresh(charge)

        return {
            "ok": True,
            "created": True,
            "event": event,
            "asaas_payment_id": asaas_payment_id,
            "charge_id": str(charge.id),
        }

    # 5) Se não existe e não é create, ignora
    if not charge:
        return {"ok": True, "ignored": True, "reason": "payment not mapped", "event": event, "asaas_payment_id": asaas_payment_id}

    # 6) Atualiza campos “sempre que vier”
    if value is not None:
        charge.value = value
    if due_date is not None:
        charge.due_date = due_date

    # Atualiza URLs se vierem
    if invoice_url:
        charge.invoice_url = invoice_url
    if bank_slip_url:
        charge.bank_slip_url = bank_slip_url
    if pdf_url:
        charge.pdf_url = pdf_url

    # 7) Normaliza status por evento (MVP)
    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED_IN_CASH"):
        charge.status = "PAID"
        charge.paid_at = now

        # regra simples: pago => zera saldo_aberto do cliente (se existir)
        c = db.query(Client).filter(Client.id == charge.client_id).first()
        if c and hasattr(c, "saldo_aberto"):
            try:
                c.saldo_aberto = 0
            except Exception:
                pass

    elif event in ("PAYMENT_OVERDUE",):
        charge.status = "OVERDUE"

    elif event in ("PAYMENT_DELETED", "PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_RECEIVED"):
        charge.status = "CANCELLED"

    else:
        # se o asaas mandar status no payment, salva pelo menos isso
        if status_raw:
            charge.status = status_raw
        else:
            charge.status = charge.status or "PENDING"

    db.add(charge)
    db.commit()

    return {"ok": True, "event": event, "asaas_payment_id": asaas_payment_id, "charge_status": charge.status}
###