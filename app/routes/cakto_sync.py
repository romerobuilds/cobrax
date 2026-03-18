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
from app.models.company import Company
from app.schemas.cakto_sync import CaktoOverviewOut, CaktoSyncResultOut
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


def _normalize_product(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cakto_product_id": str(_pick(item, "id", "_id", "product_id") or "").strip(),
        "name": _pick(item, "name", "title"),
        "product_type": _pick(item, "type", "product_type"),
        "status": _pick(item, "status"),
        "category": _pick(item, "category", "category_name"),
        "price": _to_decimal(_pick(item, "price", "amount", "value")),
        "currency": _pick(item, "currency"),
        "active": _pick(item, "active", "is_active"),
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
        "customer_email": _pick(customer, "email"),
        "customer_phone": _pick(customer, "phone", "telefone"),
        "doc_number": _pick(customer, "docNumber", "document", "cpf_cnpj"),
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