# app/routers/billing.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_current_user, get_company_for_current_user
from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.client import Client
from app.models.company import Company
from app.models.email_log import EmailLog
from app.models.user import User
from app.workers.tasks import send_email_job

from app.services.asaas_client import (
    build_external_reference,
    create_boleto_payment,
    delete_payment,
    ensure_customer,
    get_payment,
)

router = APIRouter(
    prefix="/empresas/{company_id}/cobrancas",
    tags=["Cobranças"],
    dependencies=[Depends(get_company_for_current_user)],
)


def _get_charge_or_404(db: Session, company_id: UUID, charge_id: UUID) -> BillingCharge:
    charge = (
        db.query(BillingCharge)
        .options(
            joinedload(BillingCharge.client),
            joinedload(BillingCharge.campaign),
        )
        .filter(BillingCharge.company_id == company_id, BillingCharge.id == charge_id)
        .first()
    )
    if not charge:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada")
    return charge


def _money_to_str(v: Any) -> str:
    try:
        return f"{Decimal(str(v or 0)).quantize(Decimal('0.01'))}"
    except Exception:
        return "0.00"


def _money_to_float(v: Any) -> float:
    try:
        return float(Decimal(str(v or 0)).quantize(Decimal("0.01")))
    except Exception:
        return 0.0


def _parse_due_date(value: Optional[str], fallback: Optional[date]) -> date:
    if not value:
        if fallback:
            return fallback
        return (datetime.now(timezone.utc) + timedelta(days=3)).date()

    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="due_date inválida. Use YYYY-MM-DD")


def _normalize_charge_status(status: Optional[str]) -> str:
    s = str(status or "").upper().strip()
    if s in {"RECEIVED", "CONFIRMED"}:
        return "PAID"
    if s in {"REFUNDED", "REFUND_REQUESTED"}:
        return "CANCELLED"
    return s or "PENDING"


def _serialize_charge(c: BillingCharge) -> Dict[str, Any]:
    client = getattr(c, "client", None)
    campaign = getattr(c, "campaign", None)

    return {
        "id": str(c.id),
        "company_id": str(c.company_id),
        "campaign_id": str(c.campaign_id) if c.campaign_id else None,
        "client_id": str(c.client_id) if c.client_id else None,
        "client_nome": getattr(client, "nome", None),
        "client_email": getattr(client, "email", None),
        "campaign_name": getattr(campaign, "name", None),
        "asaas_customer_id": c.asaas_customer_id,
        "asaas_payment_id": c.asaas_payment_id,
        "value": _money_to_str(c.value),
        "value_number": _money_to_float(c.value),
        "status": c.status,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "invoice_url": c.invoice_url,
        "bank_slip_url": c.bank_slip_url,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "paid_at": c.paid_at.isoformat() if c.paid_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "is_paid": str(c.status or "").upper() == "PAID",
        "is_overdue": str(c.status or "").upper() == "OVERDUE",
    }


def _build_summary(base_query) -> Dict[str, Any]:
    rows = (
        base_query.with_entities(
            BillingCharge.status,
            func.count(BillingCharge.id),
            func.coalesce(func.sum(BillingCharge.value), 0),
        )
        .group_by(BillingCharge.status)
        .all()
    )

    by_status: Dict[str, Dict[str, Any]] = {}
    total_count = 0
    total_value = Decimal("0.00")

    for status, count, value_sum in rows:
        st = str(status or "UNKNOWN").upper()
        cnt = int(count or 0)
        val = Decimal(str(value_sum or 0)).quantize(Decimal("0.01"))

        by_status[st] = {
            "count": cnt,
            "value": f"{val}",
            "value_number": float(val),
        }

        total_count += cnt
        total_value += val

    paid_count = int(by_status.get("PAID", {}).get("count", 0))
    pending_count = int(by_status.get("PENDING", {}).get("count", 0))
    overdue_count = int(by_status.get("OVERDUE", {}).get("count", 0))
    cancelled_count = int(by_status.get("CANCELLED", {}).get("count", 0))

    return {
        "total_count": total_count,
        "total_value": f"{total_value.quantize(Decimal('0.01'))}",
        "total_value_number": float(total_value.quantize(Decimal("0.01"))),
        "paid_count": paid_count,
        "pending_count": pending_count,
        "overdue_count": overdue_count,
        "cancelled_count": cancelled_count,
        "by_status": by_status,
    }


def _build_billing_email_subject(client_name: str | None, value: Any, due_date: Optional[date]) -> str:
    nome = (client_name or "Cliente").strip()
    valor = _money_to_str(value)
    venc = due_date.strftime("%d/%m/%Y") if due_date else "-"
    return f"{nome}, cobrança em aberto de R$ {valor} (venc. {venc})"


def _build_billing_email_html(charge: BillingCharge) -> str:
    client_name = getattr(charge.client, "nome", None) or "Cliente"
    valor = _money_to_str(charge.value)
    venc = charge.due_date.strftime("%d/%m/%Y") if charge.due_date else "-"
    link_pagamento = charge.invoice_url or charge.bank_slip_url or ""
    campaign_name = getattr(charge.campaign, "name", None) or "Cobrança"
    payment_id = charge.asaas_payment_id or "-"

    cta = ""
    if link_pagamento:
        cta = f"""
          <p style="margin:24px 0 8px 0;">
            <a href="{link_pagamento}"
               style="display:inline-block;padding:12px 18px;border-radius:10px;background:#22c55e;color:#0b0d10;font-weight:900;text-decoration:none;">
              Pagar agora
            </a>
          </p>
        """

    boleto_hint = ""
    if charge.bank_slip_url:
        boleto_hint = f"""
          <p style="margin:8px 0 0 0;color:#475569;">
            Boleto: <a href="{charge.bank_slip_url}" style="color:#2563eb;">abrir boleto</a>
          </p>
        """

    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111827;background:#f8fafc;padding:24px;">
      <div style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;">
        <div style="padding:22px 24px;background:linear-gradient(135deg,#052e16,#0f172a);color:#ffffff;">
          <div style="font-size:12px;opacity:.85;font-weight:700;letter-spacing:.08em;">COBRAX</div>
          <h1 style="margin:8px 0 0 0;font-size:24px;line-height:1.2;">Olá {client_name},</h1>
        </div>

        <div style="padding:24px;">
          <p style="margin:0 0 16px 0;font-size:16px;color:#111827;">
            Identificamos uma cobrança em aberto no valor de
            <b>R$ {valor}</b> com vencimento em <b>{venc}</b>.
          </p>

          <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;background:#f8fafc;">
            <p style="margin:0 0 8px 0;"><b>Referência:</b> {campaign_name}</p>
            <p style="margin:0 0 8px 0;"><b>Asaas payment ID:</b> {payment_id}</p>
            <p style="margin:0;"><b>Status:</b> {str(charge.status or '').upper()}</p>
          </div>

          {cta}
          {boleto_hint}

          <p style="margin:24px 0 0 0;color:#475569;">
            Em caso de dúvidas, responda este e-mail.
          </p>
        </div>
      </div>
    </div>
    """


def _enqueue_billing_email(db: Session, company_id: UUID, charge: BillingCharge) -> EmailLog:
    client = charge.client
    if not client or not client.email:
        raise HTTPException(status_code=400, detail="Cobrança sem cliente/e-mail para reenviar")

    subject = _build_billing_email_subject(client.nome, charge.value, charge.due_date)
    body_html = _build_billing_email_html(charge)

    log = EmailLog(
        company_id=company_id,
        client_id=charge.client_id,
        template_id=None,
        status="PENDING",
        to_email=client.email,
        to_name=client.nome,
        subject_rendered=subject,
        body_rendered=body_html,
        error_message=None,
        campaign_id=charge.campaign_id,
        campaign_run_id=None,
        should_attach_pdf=bool(charge.bank_slip_url),
        asaas_bank_slip_url=charge.bank_slip_url or None,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    send_email_job.delay(str(log.id))
    return log


@router.get("/")
def list_billing_charges(
    company_id: UUID,
    status: Optional[str] = Query(default=None, description="Ex: PENDING, PAID, OVERDUE, CANCELLED"),
    search: Optional[str] = Query(default=None, description="Busca por nome, email, payment id"),
    campaign_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = (
        db.query(BillingCharge)
        .outerjoin(Client, BillingCharge.client_id == Client.id)
        .options(
            joinedload(BillingCharge.client),
            joinedload(BillingCharge.campaign),
        )
        .filter(BillingCharge.company_id == company_id)
    )

    if status:
        q = q.filter(BillingCharge.status == status.strip().upper())

    if campaign_id:
        q = q.filter(BillingCharge.campaign_id == campaign_id)

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            or_(
                Client.nome.ilike(term),
                Client.email.ilike(term),
                BillingCharge.asaas_payment_id.ilike(term),
                BillingCharge.asaas_customer_id.ilike(term),
                BillingCharge.status.ilike(term),
            )
        )

    summary = _build_summary(q)
    total = q.count()

    items = (
        q.order_by(
            BillingCharge.created_at.desc(),
            BillingCharge.updated_at.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "summary": summary,
        "items": [_serialize_charge(c) for c in items],
    }


@router.get("/{charge_id}")
def get_billing_charge(
    company_id: UUID,
    charge_id: UUID,
    db: Session = Depends(get_db),
):
    charge = _get_charge_or_404(db, company_id, charge_id)
    return {
        "ok": True,
        "item": _serialize_charge(charge),
    }


@router.post("/{charge_id}/sync")
def sync_billing_charge(
    company_id: UUID,
    charge_id: UUID,
    db: Session = Depends(get_db),
):
    charge = _get_charge_or_404(db, company_id, charge_id)

    if not charge.asaas_payment_id:
        raise HTTPException(status_code=400, detail="Cobrança sem asaas_payment_id para sincronizar")

    payment = get_payment(str(charge.asaas_payment_id))
    now = datetime.now(timezone.utc)

    charge.status = _normalize_charge_status(payment.get("status"))
    charge.invoice_url = payment.get("invoiceUrl") or charge.invoice_url
    charge.bank_slip_url = payment.get("bankSlipUrl") or charge.bank_slip_url

    due_date_raw = payment.get("dueDate")
    if due_date_raw:
        try:
            charge.due_date = datetime.strptime(str(due_date_raw), "%Y-%m-%d").date()
        except Exception:
            pass

    if charge.status == "PAID" and not charge.paid_at:
        charge.paid_at = now

    charge.updated_at = now
    db.add(charge)
    db.commit()
    db.refresh(charge)

    return {
        "ok": True,
        "message": "Cobrança sincronizada com Asaas",
        "item": _serialize_charge(charge),
    }


@router.post("/{charge_id}/cancel")
def cancel_billing_charge(
    company_id: UUID,
    charge_id: UUID,
    db: Session = Depends(get_db),
):
    charge = _get_charge_or_404(db, company_id, charge_id)

    if str(charge.status or "").upper() == "PAID":
        raise HTTPException(status_code=400, detail="Cobrança já está paga e não pode ser cancelada")

    if charge.asaas_payment_id:
        delete_payment(str(charge.asaas_payment_id))

    charge.status = "CANCELLED"
    charge.updated_at = datetime.now(timezone.utc)
    db.add(charge)
    db.commit()
    db.refresh(charge)

    return {
        "ok": True,
        "message": "Cobrança cancelada com sucesso",
        "item": _serialize_charge(charge),
    }


@router.post("/{charge_id}/mark-paid")
def mark_billing_charge_paid_manually(
    company_id: UUID,
    charge_id: UUID,
    db: Session = Depends(get_db),
):
    charge = _get_charge_or_404(db, company_id, charge_id)

    charge.status = "PAID"
    charge.paid_at = datetime.now(timezone.utc)
    charge.updated_at = datetime.now(timezone.utc)
    db.add(charge)

    if charge.client_id:
        client = db.query(Client).filter(Client.id == charge.client_id).first()
        if client:
            try:
                current = Decimal(str(client.saldo_aberto or 0))
                value = Decimal(str(charge.value or 0))
                new_balance = current - value
                if new_balance < Decimal("0.00"):
                    new_balance = Decimal("0.00")
                client.saldo_aberto = new_balance.quantize(Decimal("0.01"))
                db.add(client)
            except Exception:
                pass

    db.commit()
    db.refresh(charge)

    return {
        "ok": True,
        "message": "Cobrança marcada como paga manualmente",
        "item": _serialize_charge(charge),
    }


@router.post("/{charge_id}/resend-email")
def resend_billing_charge_email(
    company_id: UUID,
    charge_id: UUID,
    db: Session = Depends(get_db),
):
    charge = _get_charge_or_404(db, company_id, charge_id)
    log = _enqueue_billing_email(db, company_id, charge)

    return {
        "ok": True,
        "message": "E-mail da cobrança reenviado para a fila",
        "log_id": str(log.id),
        "item": _serialize_charge(charge),
    }


@router.post("/{charge_id}/reissue")
def reissue_billing_charge(
    company_id: UUID,
    charge_id: UUID,
    body: Dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
):
    charge = _get_charge_or_404(db, company_id, charge_id)

    client = charge.client
    if not client:
        raise HTTPException(status_code=400, detail="Cobrança sem cliente vinculado")

    if not client.email:
        raise HTTPException(status_code=400, detail="Cliente sem e-mail")

    if not getattr(client, "cpf_cnpj", None):
        raise HTTPException(status_code=400, detail="Cliente sem CPF/CNPJ")

    try:
        old_status = str(charge.status or "").upper()
        if old_status != "PAID" and charge.asaas_payment_id:
            try:
                delete_payment(str(charge.asaas_payment_id))
            except Exception:
                pass

        due_date = _parse_due_date(body.get("due_date"), charge.due_date)
        value = Decimal(str(body.get("value") or charge.value or 0)).quantize(Decimal("0.01"))
        if value <= 0:
            raise HTTPException(status_code=400, detail="value deve ser maior que zero")

        campaign_name = getattr(charge.campaign, "name", None) or "Cobrança"
        description = str(body.get("description") or f"Cobrança COBRAX • {campaign_name}")

        customer_id = ensure_customer(
            name=client.nome,
            email=client.email,
            cpf_cnpj=getattr(client, "cpf_cnpj", None),
        )

        payment = create_boleto_payment(
            customer_id=customer_id,
            value=value,
            due_date=due_date,
            description=description,
            external_reference=build_external_reference(str(company_id), str(client.id)),
        )

        charge.asaas_customer_id = str(customer_id)
        charge.asaas_payment_id = str(payment.get("id") or "") or None
        charge.value = value
        charge.status = _normalize_charge_status(payment.get("status") or "PENDING")
        charge.due_date = due_date
        charge.invoice_url = payment.get("invoiceUrl")
        charge.bank_slip_url = payment.get("bankSlipUrl")
        charge.paid_at = None if charge.status != "PAID" else charge.paid_at
        charge.updated_at = datetime.now(timezone.utc)

        db.add(charge)
        db.commit()
        db.refresh(charge)

        return {
            "ok": True,
            "message": "Cobrança reemitida com sucesso",
            "item": _serialize_charge(charge),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao reemitir cobrança: {e}")