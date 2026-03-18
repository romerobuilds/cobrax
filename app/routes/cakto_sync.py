from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_company_for_current_user
from app.database_.database import get_db
from app.models.cakto_order import CaktoOrder
from app.models.cakto_product import CaktoProduct
from app.models.client import Client
from app.models.company import Company
from app.schemas.cakto_sync import (
    CaktoOverviewOut,
    CaktoSyncResultOut,
    CaktoCustomerSyncResultOut,
)
from app.services.cakto_client import get_access_token, list_all_orders, list_all_products

router = APIRouter(
    prefix="/empresas/{company_id}/cakto",
    tags=["Cakto"],
    dependencies=[Depends(get_company_for_current_user)],
)


def _get_company_or_404(db: Session, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _ensure_company_cakto(company: Company) -> None:
    if not (company.cakto_client_id or "").strip():
        raise HTTPException(status_code=400, detail="Cakto não configurada para esta empresa")

    if not (company.cakto_client_secret or "").strip():
        raise HTTPException(status_code=400, detail="Cakto não configurada para esta empresa")


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None

    s = str(value).strip()
    if not s:
        return None

    s = s.replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _pick(d: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in d and d.get(key) is not None:
            return d.get(key)
    return None


def _sanitize_cpf_cnpj(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = "".join(ch for ch in str(val).strip() if ch.isdigit())
    if not s:
        return None
    if len(s) not in (11, 14):
        return None
    return s


def _normalize_product(item: Dict[str, Any]) -> Dict[str, Any]:
    raw_category = _pick(item, "category", "category_name")
    category_value = None

    if isinstance(raw_category, dict):
        category_value = (
            raw_category.get("name")
            or raw_category.get("title")
            or raw_category.get("id")
        )
    elif raw_category is not None:
        category_value = str(raw_category).strip() or None

    raw_active = _pick(item, "active", "is_active")
    active_value = None
    if isinstance(raw_active, bool):
        active_value = raw_active
    elif raw_active is not None:
        s = str(raw_active).strip().lower()
        if s in {"true", "1", "yes", "sim", "active"}:
            active_value = True
        elif s in {"false", "0", "no", "nao", "não", "inactive"}:
            active_value = False

    return {
        "cakto_product_id": str(_pick(item, "id", "_id", "product_id") or "").strip(),
        "name": str(_pick(item, "name", "title") or "").strip() or None,
        "product_type": str(_pick(item, "type", "product_type") or "").strip() or None,
        "status": str(_pick(item, "status") or "").strip() or None,
        "category": category_value,
        "price": _to_decimal(_pick(item, "price", "amount", "value")),
        "currency": str(_pick(item, "currency") or "").strip() or None,
        "active": active_value,
        "raw_payload": item,
    }

def _normalize_order(item: Dict[str, Any]) -> Dict[str, Any]:
    customer = item.get("customer") or {}
    product = item.get("product") or {}
    utm = item.get("utm") or {}

    return {
        "cakto_order_id": str(_pick(item, "id", "_id", "order_id") or "").strip(),
        "cakto_product_id": str(
            _pick(product, "id", "_id", "product_id") or _pick(item, "product_id") or ""
        ).strip()
        or None,
        "customer_name": _pick(customer, "name", "full_name"),
        "customer_email": (_pick(customer, "email") or "").strip().lower() or None,
        "customer_phone": _pick(customer, "phone", "telefone"),
        "doc_number": _sanitize_cpf_cnpj(_pick(customer, "docNumber", "document", "cpf_cnpj")),
        "status": _pick(item, "status"),
        "payment_method": _pick(item, "paymentMethod", "payment_method"),
        "amount": _to_decimal(_pick(item, "amount", "price", "value", "total")),
        "currency": _pick(item, "currency"),
        "offer_type": _pick(item, "offer_type", "offerType"),
        "utm_source": _pick(item, "utm_source") or _pick(utm, "source"),
        "utm_medium": _pick(item, "utm_medium") or _pick(utm, "medium"),
        "utm_campaign": _pick(item, "utm_campaign") or _pick(utm, "campaign"),
        "paid_at": _parse_dt(_pick(item, "paidAt", "paid_at")),
        "canceled_at": _parse_dt(_pick(item, "canceledAt", "canceled_at")),
        "refunded_at": _parse_dt(_pick(item, "refundedAt", "refunded_at")),
        "order_created_at": _parse_dt(_pick(item, "createdAt", "created_at")),
        "raw_payload": item,
    }


@router.get("/overview", response_model=CaktoOverviewOut, status_code=status.HTTP_200_OK)
def get_cakto_overview(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)

    products_count = (
        db.query(CaktoProduct)
        .filter(CaktoProduct.company_id == company_id)
        .count()
    )
    orders_count = (
        db.query(CaktoOrder)
        .filter(CaktoOrder.company_id == company_id)
        .count()
    )

    return CaktoOverviewOut(
        ok=True,
        products_count=int(products_count),
        orders_count=int(orders_count),
        last_sync_at=company.cakto_last_sync_at.isoformat() if company.cakto_last_sync_at else None,
    )


@router.post("/sync-products", response_model=CaktoSyncResultOut, status_code=status.HTTP_200_OK)
def sync_cakto_products(
    company_id: UUID,
    page_size: int = Query(default=100, ge=1, le=100),
    max_pages: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)
    _ensure_company_cakto(company)

    try:
        token_data = get_access_token(
            client_id=company.cakto_client_id,
            client_secret=company.cakto_client_secret,
        )
        access_token = str(token_data.get("access_token") or "").strip()
        result = list_all_products(access_token, page_size=page_size, max_pages=max_pages)

        created = 0
        updated = 0

        for raw_item in result["items"]:
            norm = _normalize_product(raw_item)
            external_id = norm["cakto_product_id"]
            if not external_id:
                continue

            existing = (
                db.query(CaktoProduct)
                .filter(
                    CaktoProduct.company_id == company_id,
                    CaktoProduct.cakto_product_id == external_id,
                )
                .first()
            )

            if existing:
                existing.name = norm["name"]
                existing.product_type = norm["product_type"]
                existing.status = norm["status"]
                existing.category = norm["category"]
                existing.price = norm["price"]
                existing.currency = norm["currency"]
                existing.active = norm["active"]
                existing.raw_payload = norm["raw_payload"]
                db.add(existing)
                updated += 1
            else:
                obj = CaktoProduct(
                    company_id=company_id,
                    cakto_product_id=external_id,
                    name=norm["name"],
                    product_type=norm["product_type"],
                    status=norm["status"],
                    category=norm["category"],
                    price=norm["price"],
                    currency=norm["currency"],
                    active=norm["active"],
                    raw_payload=norm["raw_payload"],
                )
                db.add(obj)
                created += 1

        company.cakto_last_sync_at = datetime.now(timezone.utc)
        db.add(company)
        db.commit()

        return CaktoSyncResultOut(
            ok=True,
            synced=created + updated,
            created=created,
            updated=updated,
            pages=int(result["pages"]),
            message="Produtos da Cakto sincronizados com sucesso",
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Falha ao sincronizar produtos da Cakto: {e}")


@router.post("/sync-orders", response_model=CaktoSyncResultOut, status_code=status.HTTP_200_OK)
def sync_cakto_orders(
    company_id: UUID,
    page_size: int = Query(default=100, ge=1, le=100),
    max_pages: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)
    _ensure_company_cakto(company)

    try:
        token_data = get_access_token(
            client_id=company.cakto_client_id,
            client_secret=company.cakto_client_secret,
        )
        access_token = str(token_data.get("access_token") or "").strip()
        result = list_all_orders(access_token, page_size=page_size, max_pages=max_pages)

        created = 0
        updated = 0

        for raw_item in result["items"]:
            norm = _normalize_order(raw_item)
            external_id = norm["cakto_order_id"]
            if not external_id:
                continue

            existing = (
                db.query(CaktoOrder)
                .filter(
                    CaktoOrder.company_id == company_id,
                    CaktoOrder.cakto_order_id == external_id,
                )
                .first()
            )

            if existing:
                existing.cakto_product_id = norm["cakto_product_id"]
                existing.customer_name = norm["customer_name"]
                existing.customer_email = norm["customer_email"]
                existing.customer_phone = norm["customer_phone"]
                existing.doc_number = norm["doc_number"]
                existing.status = norm["status"]
                existing.payment_method = norm["payment_method"]
                existing.amount = norm["amount"]
                existing.currency = norm["currency"]
                existing.offer_type = norm["offer_type"]
                existing.utm_source = norm["utm_source"]
                existing.utm_medium = norm["utm_medium"]
                existing.utm_campaign = norm["utm_campaign"]
                existing.paid_at = norm["paid_at"]
                existing.canceled_at = norm["canceled_at"]
                existing.refunded_at = norm["refunded_at"]
                existing.order_created_at = norm["order_created_at"]
                existing.raw_payload = norm["raw_payload"]
                db.add(existing)
                updated += 1
            else:
                obj = CaktoOrder(
                    company_id=company_id,
                    cakto_order_id=external_id,
                    cakto_product_id=norm["cakto_product_id"],
                    customer_name=norm["customer_name"],
                    customer_email=norm["customer_email"],
                    customer_phone=norm["customer_phone"],
                    doc_number=norm["doc_number"],
                    status=norm["status"],
                    payment_method=norm["payment_method"],
                    amount=norm["amount"],
                    currency=norm["currency"],
                    offer_type=norm["offer_type"],
                    utm_source=norm["utm_source"],
                    utm_medium=norm["utm_medium"],
                    utm_campaign=norm["utm_campaign"],
                    paid_at=norm["paid_at"],
                    canceled_at=norm["canceled_at"],
                    refunded_at=norm["refunded_at"],
                    order_created_at=norm["order_created_at"],
                    raw_payload=norm["raw_payload"],
                )
                db.add(obj)
                created += 1

        company.cakto_last_sync_at = datetime.now(timezone.utc)
        db.add(company)
        db.commit()

        return CaktoSyncResultOut(
            ok=True,
            synced=created + updated,
            created=created,
            updated=updated,
            pages=int(result["pages"]),
            message="Pedidos da Cakto importados com sucesso",
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Falha ao importar pedidos da Cakto: {e}")


@router.post("/sync-customers", response_model=CaktoCustomerSyncResultOut, status_code=status.HTTP_200_OK)
def sync_cakto_customers(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    company = _get_company_or_404(db, company_id)
    _ensure_company_cakto(company)

    orders = (
        db.query(CaktoOrder)
        .filter(CaktoOrder.company_id == company_id)
        .order_by(
            CaktoOrder.order_created_at.desc().nullslast(),
            CaktoOrder.created_at.desc(),
        )
        .all()
    )

    if not orders:
        return CaktoCustomerSyncResultOut(
            ok=True,
            created=0,
            updated=0,
            skipped_no_email=0,
            skipped_unchanged=0,
            scanned_orders=0,
            message="Nenhum pedido da Cakto encontrado para criar clientes",
        )

    created = 0
    updated = 0
    skipped_no_email = 0
    skipped_unchanged = 0

    seen_emails: set[str] = set()

    for order in orders:
        email = (order.customer_email or "").strip().lower()
        if not email:
            skipped_no_email += 1
            continue

        if email in seen_emails:
            continue
        seen_emails.add(email)

        existing = (
            db.query(Client)
            .filter(
                Client.company_id == company_id,
                Client.email == email,
            )
            .first()
        )

        customer_name = (order.customer_name or "").strip() or email.split("@")[0]
        customer_phone = (order.customer_phone or "").strip() or None
        doc_number = _sanitize_cpf_cnpj(order.doc_number)
        order_ref = str(order.cakto_order_id or "").strip() or None

        if existing:
            changed = False

            if not (existing.nome or "").strip() and customer_name:
                existing.nome = customer_name
                changed = True

            if not (existing.telefone or "").strip() and customer_phone:
                existing.telefone = customer_phone
                changed = True

            if not (existing.cpf_cnpj or "").strip() and doc_number:
                existing.cpf_cnpj = doc_number
                changed = True

            if not (existing.source_system or "").strip():
                existing.source_system = "CAKTO"
                changed = True

            if order_ref and not (existing.source_external_ref or "").strip():
                existing.source_external_ref = order_ref
                changed = True

            if order.order_created_at and (
                existing.last_order_at is None or order.order_created_at > existing.last_order_at
            ):
                existing.last_order_at = order.order_created_at
                changed = True

            if changed:
                db.add(existing)
                updated += 1
            else:
                skipped_unchanged += 1

            continue

        new_client = Client(
            nome=customer_name,
            email=email,
            telefone=customer_phone,
            cpf_cnpj=doc_number,
            owner_id=company.owner_id,
            company_id=company_id,
            is_mensalista=False,
            saldo_aberto=Decimal("0.00"),
            source_system="CAKTO",
            source_external_ref=order_ref,
            last_order_at=order.order_created_at,
        )
        db.add(new_client)
        created += 1

    db.commit()

    return CaktoCustomerSyncResultOut(
        ok=True,
        created=created,
        updated=updated,
        skipped_no_email=skipped_no_email,
        skipped_unchanged=skipped_unchanged,
        scanned_orders=len(orders),
        message="Clientes criados/atualizados a partir dos pedidos da Cakto",
    )