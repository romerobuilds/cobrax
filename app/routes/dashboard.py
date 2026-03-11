from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import Date, and_, cast, case, func, or_
from sqlalchemy.orm import Session

from app.core.jwt import verificar_token
from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.campaign import Campaign
from app.models.client import Client
from app.models.company import Company
from app.models.email_log import EmailLog
from app.models.email_template import EmailTemplate
from app.models.user import User

router = APIRouter(prefix="/empresas", tags=["Dashboard"])


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    token = parts[1].strip()
    payload = verificar_token(token)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token sem 'sub'")

    try:
        user_uuid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail="Token 'sub' inválido (UUID esperado)")

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")

    return user


def _get_company_or_403(db: Session, company_id: str, user: User) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if str(company.owner_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem acesso a essa empresa")

    return company


def _normalize_billing_status(status: Optional[str], due_date, today) -> str:
    s = str(status or "").upper().strip()

    if s in {"RECEIVED", "CONFIRMED"}:
        return "PAID"

    if s in {"REFUNDED", "REFUND_REQUESTED"}:
        return "CANCELLED"

    if s not in {"PAID", "CANCELLED"} and due_date and due_date < today:
        return "OVERDUE"

    return s or "PENDING"


@router.get("/{company_id}/dashboard/metrics")
def dashboard_metrics(
    company_id: str,
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_403(db, company_id, user)

    days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    total_clients = (
        db.query(func.count(Client.id))
        .filter(Client.company_id == company_id)
        .scalar()
        or 0
    )

    total_templates = (
        db.query(func.count(EmailTemplate.id))
        .filter(EmailTemplate.company_id == company_id)
        .scalar()
        or 0
    )

    total_campaigns = (
        db.query(func.count(Campaign.id))
        .filter(Campaign.company_id == company_id)
        .scalar()
        or 0
    )

    q_logs_period = db.query(EmailLog).filter(
        EmailLog.company_id == company_id,
        EmailLog.created_at >= since,
    )

    sent_period = q_logs_period.filter(func.upper(EmailLog.status) == "SENT").count()
    failed_period = q_logs_period.filter(func.upper(EmailLog.status) == "FAILED").count()

    pending_period = q_logs_period.filter(
        func.upper(EmailLog.status).notin_(["SENT", "FAILED"])
    ).count()

    total_period = q_logs_period.count()

    success_rate = 0.0
    if (sent_period + failed_period) > 0:
        success_rate = round((sent_period / (sent_period + failed_period)) * 100.0, 2)

    rows = (
        db.query(
            cast(func.date_trunc("day", EmailLog.created_at), Date).label("day"),
            func.sum(
                case((func.upper(EmailLog.status) == "SENT", 1), else_=0)
            ).label("sent"),
            func.sum(
                case((func.upper(EmailLog.status) == "FAILED", 1), else_=0)
            ).label("failed"),
            func.sum(
                case((func.upper(EmailLog.status).notin_(["SENT", "FAILED"]), 1), else_=0)
            ).label("pending"),
            func.count(EmailLog.id).label("total"),
        )
        .filter(
            EmailLog.company_id == company_id,
            EmailLog.created_at >= since,
        )
        .group_by("day")
        .order_by("day")
        .all()
    )

    series_map: Dict[str, Dict[str, int]] = {}
    for r in rows:
        key = r.day.isoformat() if r.day else None
        if not key:
            continue
        series_map[key] = {
            "sent": int(r.sent or 0),
            "failed": int(r.failed or 0),
            "pending": int(r.pending or 0),
            "total": int(r.total or 0),
        }

    series: List[Dict[str, Any]] = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        v = series_map.get(d, {"sent": 0, "failed": 0, "pending": 0, "total": 0})
        series.append({"date": d, **v})

    recent_campaigns = (
        db.query(Campaign)
        .filter(Campaign.company_id == company_id)
        .order_by(Campaign.created_at.desc())
        .limit(10)
        .all()
    )

    campaigns_out = [
        {
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "mode": getattr(c, "mode", None),
            "rate_per_min": getattr(c, "rate_per_min", None),
            "template_id": str(c.template_id) if getattr(c, "template_id", None) else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "next_run_at": c.next_run_at.isoformat() if getattr(c, "next_run_at", None) else None,
        }
        for c in recent_campaigns
    ]

    recent_logs = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .order_by(EmailLog.created_at.desc())
        .limit(20)
        .all()
    )

    logs_out = [
        {
            "id": str(l.id),
            "status": l.status,
            "to_email": l.to_email,
            "subject": l.subject_rendered,
            "error": l.error_message,
            "campaign_id": str(l.campaign_id) if l.campaign_id else None,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in recent_logs
    ]

    return {
        "range_days": days,
        "company": {"id": str(company.id), "nome": company.nome},
        "kpis": {
            "clients": int(total_clients),
            "templates": int(total_templates),
            "campaigns": int(total_campaigns),
            "sent": int(sent_period),
            "failed": int(failed_period),
            "pending": int(pending_period),
            "total": int(total_period),
            "success_rate": success_rate,
        },
        "series": series,
        "recent_campaigns": campaigns_out,
        "recent_sends": logs_out,
        "updated_at": now.isoformat(),
    }


@router.get("/{company_id}/dashboard/finance")
def dashboard_finance(
    company_id: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_403(db, company_id, user)

    limit = max(1, min(int(limit or 10), 50))
    today = datetime.now(timezone.utc).date()

    charges = (
        db.query(BillingCharge, Client)
        .outerjoin(Client, Client.id == BillingCharge.client_id)
        .filter(BillingCharge.company_id == company_id)
        .order_by(BillingCharge.created_at.desc())
        .all()
    )

    emitido = 0.0
    pago = 0.0
    pendente = 0.0
    vencido = 0.0
    cancelado = 0.0

    by_status_map: Dict[str, Dict[str, Any]] = {}

    recent_charges = []

    for charge, client in charges:
        value = float(charge.value or 0)
        normalized_status = _normalize_billing_status(charge.status, charge.due_date, today)

        emitido += value

        if normalized_status == "PAID":
          pago += value
        elif normalized_status == "PENDING":
          pendente += value
        elif normalized_status == "OVERDUE":
          vencido += value
        elif normalized_status == "CANCELLED":
          cancelado += value

        if normalized_status not in by_status_map:
            by_status_map[normalized_status] = {
                "status": normalized_status,
                "count": 0,
                "amount": 0.0,
            }

        by_status_map[normalized_status]["count"] += 1
        by_status_map[normalized_status]["amount"] += value

    recent = charges[:limit]

    for charge, client in recent:
        normalized_status = _normalize_billing_status(charge.status, charge.due_date, today)
        overdue = normalized_status == "OVERDUE"

        recent_charges.append(
            {
                "id": str(charge.id),
                "campaign_id": str(charge.campaign_id) if charge.campaign_id else None,
                "client_id": str(charge.client_id) if charge.client_id else None,
                "client_nome": getattr(client, "nome", None) if client else None,
                "client_email": getattr(client, "email", None) if client else None,
                "asaas_payment_id": charge.asaas_payment_id,
                "value": float(charge.value or 0),
                "status": normalized_status,
                "due_date": charge.due_date.isoformat() if charge.due_date else None,
                "invoice_url": charge.invoice_url,
                "bank_slip_url": charge.bank_slip_url,
                "created_at": charge.created_at.isoformat() if charge.created_at else None,
                "paid_at": charge.paid_at.isoformat() if charge.paid_at else None,
                "overdue": overdue,
            }
        )

    status_order = {"PAID": 0, "PENDING": 1, "OVERDUE": 2, "CANCELLED": 3}
    by_status = sorted(
        list(by_status_map.values()),
        key=lambda item: status_order.get(item["status"], 99)
    )

    return {
        "summary": {
            "emitido": round(float(emitido), 2),
            "pago": round(float(pago), 2),
            "pendente": round(float(pendente), 2),
            "vencido": round(float(vencido), 2),
            "cancelado": round(float(cancelado), 2),
        },
        "by_status": by_status,
        "recent_charges": recent_charges,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }