from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_company_for_current_user
from app.database_.database import get_db
from app.models.cakto_automation import CaktoAutomation
from app.models.cakto_order import CaktoOrder
from app.models.company import Company
from app.schemas.cakto_automation import (
    CaktoAutomationCreate,
    CaktoAutomationOut,
    CaktoAutomationRunResultOut,
    CaktoAutomationUpdate,
)
from app.routes.cakto_sync import sync_customers_from_orders_query

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

    obj = CaktoAutomation(
        company_id=company_id,
        name=payload.name.strip(),
        is_active=bool(payload.is_active),
        event_type=payload.event_type,
        action_type=payload.action_type,
        cakto_product_id=(payload.cakto_product_id or "").strip() or None,
        run_on_status_paid=bool(payload.run_on_status_paid),
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

    orders_query = db.query(CaktoOrder).filter(CaktoOrder.company_id == company_id)

    if obj.run_on_status_paid:
        orders_query = orders_query.filter(CaktoOrder.status.ilike("paid"))

    if (obj.cakto_product_id or "").strip():
        orders_query = orders_query.filter(CaktoOrder.cakto_product_id == obj.cakto_product_id.strip())

    if obj.action_type != "sync_customer":
        raise HTTPException(status_code=400, detail="Ação da automação ainda não suportada")

    result = sync_customers_from_orders_query(
        db=db,
        company_id=company_id,
        company=company,
        orders_query=orders_query,
    )

    obj.last_run_at = datetime.now(timezone.utc)
    obj.updated_at = datetime.now(timezone.utc)
    db.add(obj)
    db.commit()
    db.refresh(obj)

    return CaktoAutomationRunResultOut(
        ok=True,
        automation_id=str(obj.id),
        matched_orders=result["scanned_orders"],
        created=result["created"],
        updated=result["updated"],
        skipped_no_email=result["skipped_no_email"],
        skipped_unchanged=result["skipped_unchanged"],
        message="Automação executada com sucesso",
    )