# app/routers/webhooks_asaas.py
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

# IMPORTANTE:
# No seu NGINX você expõe /api/* -> FastAPI.
# Então no Asaas você usa: https://cobrax.tech/api/webhook/asaas
# E aqui dentro do FastAPI o prefix deve ser: /webhook/asaas
router = APIRouter(prefix="/webhook/asaas", tags=["Webhooks - Asaas"])


def _expected_token() -> str:
    return (os.getenv("ASAAS_WEBHOOK_TOKEN") or "").strip()


def _parse_date(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    # Asaas geralmente manda YYYY-MM-DD
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None


def _is_uuid(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text.strip()))


def _try_parse_external_reference(ext: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Espera algo como:
      company:<uuid>|client:<uuid>
    Retorna (company_id, client_id)
    """
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

    # dados úteis
    status = payment.get("status") or "PENDING"
    value = payment.get("value") or 0
    due_date = _parse_date(payment.get("dueDate"))
    invoice_url = payment.get("invoiceUrl")
    bank_slip_url = payment.get("bankSlipUrl")
    pdf_url = payment.get("invoiceUrl")  # se quiser outro campo, ajuste aqui
    external_reference = (payment.get("externalReference") or "").strip() or None

    now = datetime.now(timezone.utc)

    # 1) tenta achar cobrança existente por asaas_payment_id
    charge: BillingCharge | None = (
        db.query(BillingCharge)
        .filter(BillingCharge.asaas_payment_id == str(payment_id))
        .first()
    )

    # 2) Se não achou por payment_id e veio externalReference UUID,
    # tenta mapear por BillingCharge.id (Opção B - recomendada)
    if not charge and external_reference and _is_uuid(external_reference):
        maybe_charge = db.query(BillingCharge).filter(BillingCharge.id == external_reference).first()
        if maybe_charge:
            maybe_charge.asaas_payment_id = str(payment_id)
            maybe_charge.status = str(status)
            maybe_charge.value = value
            maybe_charge.due_date = due_date
            maybe_charge.invoice_url = invoice_url
            maybe_charge.bank_slip_url = bank_slip_url
            maybe_charge.pdf_url = pdf_url
            maybe_charge.updated_at = now

            db.add(maybe_charge)
            db.commit()
            db.refresh(maybe_charge)
            charge = maybe_charge

    # 3) se ainda não existe e o evento é criação, tenta criar usando company/client no externalReference
    if not charge and event == "PAYMENT_CREATED":
        company_id, client_id = _try_parse_external_reference(external_reference)

        if not client_id:
            # fallback simples (MVP): tente mapear por alguma info que você salve no Client
            # Exemplo (se existir no seu model): client = db.query(Client).filter(Client.asaas_customer_id == payment.get("customer")).first()
            client = None
        else:
            client = db.query(Client).filter(Client.id == client_id).first()

        if not client:
            # Não tem como mapear -> não cria (mas responde OK para não quebrar o Asaas)
            return {
                "ok": True,
                "ignored": True,
                "reason": "cannot map PAYMENT_CREATED to a Client (missing externalReference mapping)",
                "event": event,
                "payment_id": str(payment_id),
            }

        # se não veio company_id, pega da própria relação do client
        final_company_id = company_id or str(getattr(client, "company_id", None))
        if not final_company_id or final_company_id == "None":
            return {
                "ok": True,
                "ignored": True,
                "reason": "client has no company_id",
                "event": event,
                "payment_id": str(payment_id),
            }

        charge = BillingCharge(
            company_id=final_company_id,
            client_id=client.id,
            asaas_payment_id=str(payment_id),
            status=str(status),
            value=value,
            due_date=due_date,
            invoice_url=invoice_url,
            bank_slip_url=bank_slip_url,
            pdf_url=pdf_url,
            created_at=now,
            updated_at=now,
        )
        db.add(charge)
        db.commit()
        db.refresh(charge)

    # 4) se ainda não existe, ignora (não mapeado)
    if not charge:
        return {
            "ok": True,
            "ignored": True,
            "reason": "payment not mapped",
            "event": event,
            "payment_id": str(payment_id),
            "externalReference": external_reference,
        }

    # 5) atualiza status por evento
    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        charge.status = "PAID"
        charge.paid_at = now

        # regra simples (MVP): pago => zera saldo_aberto do cliente
        c = db.query(Client).filter(Client.id == charge.client_id).first()
        if c:
            c.saldo_aberto = 0

    elif event in ("PAYMENT_OVERDUE",):
        charge.status = "OVERDUE"

    elif event in ("PAYMENT_DELETED", "PAYMENT_REFUNDED"):
        charge.status = "CANCELLED"

    # sempre tenta atualizar links/status também
    charge.value = value
    charge.due_date = due_date
    charge.invoice_url = invoice_url
    charge.bank_slip_url = bank_slip_url
    charge.pdf_url = pdf_url
    charge.updated_at = now

    db.add(charge)
    db.commit()

    return {"ok": True, "event": event, "payment_id": str(payment_id), "charge_id": str(charge.id)}