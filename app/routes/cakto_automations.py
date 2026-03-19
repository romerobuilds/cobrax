from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_company_for_current_user
from app.core.template_vars import build_default_context
from app.database_.database import get_db
from app.models.cakto_automation import CaktoAutomation
from app.models.cakto_order import CaktoOrder
from app.models.client import Client
from app.models.company import Company
from app.models.email_log import EmailLog
from app.models.email_template import EmailTemplate
from app.schemas.cakto_automation import (
    CaktoAutomationCreate,
    CaktoAutomationOut,
    CaktoAutomationRunResultOut,
    CaktoAutomationUpdate,
)
from app.services.template_renderer import render_email_template
from app.routes.cakto_sync import sync_customers_from_orders_query
from app.workers.tasks import send_email_job

router = APIRouter(
    prefix="/empresas/{company_id}/cakto-automations",
    tags=["Cakto"],
    dependencies=[Depends(get_company_for_current_user)],
)


def _get_company_or_404(db: Session, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _get_automation_or_404(db: Session, company_id: UUID, automation_id: UUID) -> CaktoAutomation:
    obj = (
        db.query(CaktoAutomation)
        .filter(
            CaktoAutomation.company_id == company_id,
            CaktoAutomation.id == automation_id,
        )
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Automação não encontrada")
    return obj


def _ensure_template_or_400(db: Session, company_id: UUID, template_id: UUID | None) -> EmailTemplate | None:
    if not template_id:
        return None

    tpl = (
        db.query(EmailTemplate)
        .filter(
            EmailTemplate.company_id == company_id,
            EmailTemplate.id == template_id,
        )
        .first()
    )
    if not tpl:
        raise HTTPException(status_code=400, detail="template_id inválido para esta empresa")
    return tpl


def _fmt_money_br(value) -> str | None:
    if value is None:
        return None
    try:
        dec = Decimal(str(value)).quantize(Decimal("0.01"))
        s = f"{dec:.2f}".replace(".", ",")
        return f"R$ {s}"
    except Exception:
        return str(value)


def _fmt_dt_br(value) -> str | None:
    if not value:
        return None
    try:
        dt = value
        if getattr(dt, "tzinfo", None) is None:
            return dt.strftime("%d/%m/%Y %H:%M")
        return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)


def _build_order_context(order: CaktoOrder) -> dict:
    return {
        "pedido_id": getattr(order, "cakto_order_id", None),
        "produto_id": getattr(order, "cakto_product_id", None),
        "pedido_status": getattr(order, "status", None),
        "forma_pagamento": getattr(order, "payment_method", None),
        "valor_pedido": _fmt_money_br(getattr(order, "amount", None)),
        "valor": _fmt_money_br(getattr(order, "amount", None)),
        "data_pedido": _fmt_dt_br(getattr(order, "order_created_at", None)),
        "utm_source": getattr(order, "utm_source", None),
        "utm_medium": getattr(order, "utm_medium", None),
        "utm_campaign": getattr(order, "utm_campaign", None),
        "descricao": getattr(order, "offer_type", None) or "Pedido importado da Cakto",
    }


def _queue_automation_emails(
    *,
    db: Session,
    company: Company,
    company_id: UUID,
    template: EmailTemplate,
    matched_pairs: list,
) -> int:
    queued_log_ids: list[str] = []
    emails_queued = 0
    seen_client_ids: set[str] = set()

    for pair in matched_pairs or []:
        client_id = pair.get("client_id")
        order_id = pair.get("order_id")

        if not client_id or not order_id:
            continue

        client_key = str(client_id)
        if client_key in seen_client_ids:
            continue
        seen_client_ids.add(client_key)

        client = (
            db.query(Client)
            .filter(
                Client.company_id == company_id,
                Client.id == client_id,
            )
            .first()
        )
        if not client or not (client.email or "").strip():
            continue

        order = (
            db.query(CaktoOrder)
            .filter(
                CaktoOrder.company_id == company_id,
                CaktoOrder.id == order_id,
            )
            .first()
        )
        if not order:
            continue

        ctx = build_default_context(
            company=company,
            client=client,
            extra=_build_order_context(order),
        )

        try:
            rendered = render_email_template(
                subject_tpl=template.assunto,
                body_tpl=template.corpo_html,
                context=ctx,
            )
        except Exception:
            continue

        log = EmailLog(
            company_id=company_id,
            client_id=client.id,
            template_id=template.id,
            status="PENDING",
            to_email=client.email,
            to_name=client.nome,
            subject_rendered=rendered.subject,
            body_rendered=rendered.body,
            error_message=None,
        )
        db.add(log)
        db.flush()

        queued_log_ids.append(str(log.id))
        emails_queued += 1

    db.commit()

    for log_id in queued_log_ids:
        send_email_job.delay(log_id)

    return emails_queued


def _build_orders_query_for_automation(base_query, automation: CaktoAutomation):
    q = base_query

    if automation.run_on_status_paid:
        q = q.filter(CaktoOrder.status.ilike("paid"))

    if (automation.cakto_product_id or "").strip():
        q = q.filter(CaktoOrder.cakto_product_id == automation.cakto_product_id.strip())

    return q


def _run_single_automation(
    *,
    db: Session,
    company: Company,
    company_id: UUID,
    automation: CaktoAutomation,
    base_orders_query,
):
    if automation.action_type != "sync_customer":
        return {
            "matched_orders": 0,
            "created": 0,
            "updated": 0,
            "skipped_no_email": 0,
            "skipped_unchanged": 0,
            "emails_queued": 0,
        }

    filtered_query = _build_orders_query_for_automation(base_orders_query, automation)

    result = sync_customers_from_orders_query(
        db=db,
        company_id=company_id,
        company=company,
        orders_query=filtered_query,
    )

    emails_queued = 0
    if automation.send_email_after and automation.template_id:
        tpl = _ensure_template_or_400(db, company_id, automation.template_id)
        if tpl:
            emails_queued = _queue_automation_emails(
                db=db,
                company=company,
                company_id=company_id,
                template=tpl,
                matched_pairs=result.get("matched_pairs", []),
            )

    automation.last_run_at = datetime.now(timezone.utc)
    automation.updated_at = datetime.now(timezone.utc)
    db.add(automation)
    db.commit()
    db.refresh(automation)

    return {
        "matched_orders": result["scanned_orders"],
        "created": result["created"],
        "updated": result["updated"],
        "skipped_no_email": result["skipped_no_email"],
        "skipped_unchanged": result["skipped_unchanged"],
        "emails_queued": emails_queued,
    }


def run_matching_cakto_automations(
    *,
    db: Session,
    company: Company,
    company_id: UUID,
    order_ids: list | None = None,
):
    automations = (
        db.query(CaktoAutomation)
        .filter(
            CaktoAutomation.company_id == company_id,
            CaktoAutomation.is_active.is_(True),
        )
        .order_by(CaktoAutomation.created_at.asc())
        .all()
    )

    if not automations:
        return {
            "automation_runs": 0,
            "automation_clients_created": 0,
            "automation_clients_updated": 0,
            "automation_emails_queued": 0,
        }

    base_orders_query = db.query(CaktoOrder).filter(CaktoOrder.company_id == company_id)
    if order_ids:
        base_orders_query = base_orders_query.filter(CaktoOrder.id.in_(order_ids))

    total_runs = 0
    total_created = 0
    total_updated = 0
    total_emails_queued = 0

    for automation in automations:
        result = _run_single_automation(
            db=db,
            company=company,
            company_id=company_id,
            automation=automation,
            base_orders_query=base_orders_query,
        )
        total_runs += 1
        total_created += int(result.get("created", 0))
        total_updated += int(result.get("updated", 0))
        total_emails_queued += int(result.get("emails_queued", 0))

    return {
        "automation_runs": total_runs,
        "automation_clients_created": total_created,
        "automation_clients_updated": total_updated,
        "automation_emails_queued": total_emails_queued,
    }


@router.get("/", response_model=list[CaktoAutomationOut], status_code=status.HTTP_200_OK)
def list_cakto_automations(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    _get_company_or_404(db, company_id)

    return (
        db.query(CaktoAutomation)
        .filter(CaktoAutomation.company_id == company_id)
        .order_by(CaktoAutomation.created_at.desc())
        .all()
    )


@router.post("/", response_model=CaktoAutomationOut, status_code=status.HTTP_201_CREATED)
def create_cakto_automation(
    company_id: UUID,
    payload: CaktoAutomationCreate,
    db: Session = Depends(get_db),
):
    _get_company_or_404(db, company_id)

    tpl = _ensure_template_or_400(db, company_id, payload.template_id)

    if payload.send_email_after and not tpl:
        raise HTTPException(status_code=400, detail="Selecione um template para envio automático")

    obj = CaktoAutomation(
        company_id=company_id,
        name=payload.name.strip(),
        is_active=bool(payload.is_active),
        event_type=payload.event_type,
        action_type=payload.action_type,
        cakto_product_id=(payload.cakto_product_id or "").strip() or None,
        run_on_status_paid=bool(payload.run_on_status_paid),
        send_email_after=bool(payload.send_email_after),
        template_id=tpl.id if tpl else None,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{automation_id}", response_model=CaktoAutomationOut, status_code=status.HTTP_200_OK)
def update_cakto_automation(
    company_id: UUID,
    automation_id: UUID,
    payload: CaktoAutomationUpdate,
    db: Session = Depends(get_db),
):
    obj = _get_automation_or_404(db, company_id, automation_id)

    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] is not None:
        obj.name = data["name"].strip()

    if "is_active" in data and data["is_active"] is not None:
        obj.is_active = bool(data["is_active"])

    if "event_type" in data and data["event_type"] is not None:
        obj.event_type = data["event_type"]

    if "action_type" in data and data["action_type"] is not None:
        obj.action_type = data["action_type"]

    if "cakto_product_id" in data:
        obj.cakto_product_id = (data["cakto_product_id"] or "").strip() or None

    if "run_on_status_paid" in data and data["run_on_status_paid"] is not None:
        obj.run_on_status_paid = bool(data["run_on_status_paid"])

    if "send_email_after" in data and data["send_email_after"] is not None:
        obj.send_email_after = bool(data["send_email_after"])

    if "template_id" in data:
        tpl = _ensure_template_or_400(db, company_id, data["template_id"])
        obj.template_id = tpl.id if tpl else None

    if obj.send_email_after and not obj.template_id:
        raise HTTPException(status_code=400, detail="Selecione um template para envio automático")

    obj.updated_at = datetime.now(timezone.utc)

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{automation_id}", status_code=status.HTTP_200_OK)
def delete_cakto_automation(
    company_id: UUID,
    automation_id: UUID,
    db: Session = Depends(get_db),
):
    obj = _get_automation_or_404(db, company_id, automation_id)
    db.delete(obj)
    db.commit()
    return {"ok": True, "message": "Automação removida com sucesso"}


@router.post("/{automation_id}/run", response_model=CaktoAutomationRunResultOut, status_code=status.HTTP_200_OK)
def run_cakto_automation_now(
    company_id: UUID,
    automation_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)
    obj = _get_automation_or_404(db, company_id, automation_id)

    if not obj.is_active:
        raise HTTPException(status_code=400, detail="Automação está inativa")

    base_orders_query = db.query(CaktoOrder).filter(CaktoOrder.company_id == company_id)

    result = _run_single_automation(
        db=db,
        company=company,
        company_id=company_id,
        automation=obj,
        base_orders_query=base_orders_query,
    )

    return CaktoAutomationRunResultOut(
        ok=True,
        automation_id=str(obj.id),
        matched_orders=result["matched_orders"],
        created=result["created"],
        updated=result["updated"],
        skipped_no_email=result["skipped_no_email"],
        skipped_unchanged=result["skipped_unchanged"],
        emails_queued=result["emails_queued"],
        message="Automação executada com sucesso",
    )