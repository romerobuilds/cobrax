# app/routers/billing.py
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.client import Client
from app.models.company import Company
from app.models.user import User

router = APIRouter(prefix="/empresas/{company_id}/cobrancas", tags=["Cobranças"])


def _get_company_or_404(db: Session, company_id: UUID, user_id: UUID) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user_id)
        .first()
    )
    if not company:
        raise HTTPException(
            status_code=404,
            detail="Empresa não encontrada ou não pertence a você",
        )
    return company


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


@router.get("/")
def list_billing_charges(
    company_id: UUID,
    status: Optional[str] = Query(default=None, description="Ex: PENDING, PAID, OVERDUE, CANCELLED"),
    search: Optional[str] = Query(default=None, description="Busca por nome, email, payment id"),
    campaign_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)

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
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user.id)

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

    return {
        "ok": True,
        "item": _serialize_charge(charge),
    }