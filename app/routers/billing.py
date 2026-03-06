# app/routers/billing.py
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
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
        "status": c.status,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "invoice_url": c.invoice_url,
        "bank_slip_url": c.bank_slip_url,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "paid_at": c.paid_at.isoformat() if c.paid_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
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
        q = q.filter(BillingCharge.status == status.strip())

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

    total = q.count()

    items = (
        q.order_by(BillingCharge.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
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