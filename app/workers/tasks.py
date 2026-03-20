# app/workers/tasks.py
import json
import hashlib

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from celery.exceptions import Retry

from app.workers.celery_app import celery_app
from app.database_.database import SessionLocal

from app.models.email_log import EmailLog
from app.models.company import Company
from app.models.plan import Plan
from app.models.user import User
from app.models.company_user import CompanyUser
from app.models.client import Client
from app.models.billing_charge import BillingCharge
from app.models.campaign import Campaign
from app.models.campaign_run import CampaignRun
from app.models.cakto_order import CaktoOrder
from app.models.cakto_automation import CaktoAutomation
from app.models.email_template import EmailTemplate

from app.workers.rate_limiter import throttle_company
from app.services.mailer import send_smtp_email, EmailAttachment
from app.services.asaas_client import download_url_as_bytes
from app.services.cakto_client import get_access_token, list_all_orders
from app.core.template_vars import build_default_context
from app.services.template_renderer import render_email_template
from app.models.cakto_webhook_event import CaktoWebhookEvent
from app.services.cakto_client import get_access_token, list_all_orders, retrieve_order


def _same_utc_day(dt) -> bool:
    if not dt:
        return False
    return dt.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def _seconds_until_next_utc_0005(now_utc: datetime) -> int:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    next_day_date = now_utc.date() + timedelta(days=1)
    next_run = (
        datetime.combine(next_day_date, datetime.min.time(), tzinfo=timezone.utc)
        + timedelta(minutes=5)
    )
    seconds = int((next_run - now_utc).total_seconds())
    return max(60, seconds)


def _recompute_run_totals(db, run_id: str):
    rows = (
        db.query(EmailLog.status, func.count(EmailLog.id))
        .filter(EmailLog.campaign_run_id == run_id)
        .group_by(EmailLog.status)
        .all()
    )

    by_status = {str(s): int(c) for s, c in rows}

    sent = int(by_status.get("SENT", 0))
    failed = int(by_status.get("FAILED", 0))
    cancelled = int(by_status.get("CANCELLED", 0))

    pending_like = 0
    for k in ["PENDING", "QUEUED", "SCHEDULED", "SENDING", "RETRYING", "DEFERRED"]:
        pending_like += int(by_status.get(k, 0))

    total = int(sum(by_status.values()))

    run = db.query(CampaignRun).filter(CampaignRun.id == run_id).first()
    if not run:
        return

    run.totals = {
        "total": total,
        "sent": sent,
        "failed": failed,
        "cancelled": cancelled,
        "pending": int(pending_like),
        "by_status": by_status,
    }

    if run.status in ("running", "paused") and pending_like == 0:
        if run.status != "cancelled":
            run.status = "finished"
        run.finished_at = datetime.now(timezone.utc)

        camp = db.query(Campaign).filter(Campaign.id == run.campaign_id).first()
        if camp and camp.status not in ("cancelled",):
            if bool(getattr(camp, "is_schedule_enabled", False)) and getattr(camp, "next_run_at", None) is not None:
                camp.status = "scheduled"
            else:
                camp.status = "done"

    db.commit()


def _safe_update_totals_after_status_change(db, log: EmailLog | None):
    if not log:
        return
    if getattr(log, "campaign_run_id", None):
        _recompute_run_totals(db, str(log.campaign_run_id))


def _looks_like_html(s: str) -> bool:
    if not s:
        return False
    ss = s.lower()
    return ("<html" in ss) or ("<div" in ss) or ("<p" in ss) or ("<h" in ss) or ("</" in ss)


def _strip_html_simple(html: str) -> str:
    if not html:
        return ""
    txt = re.sub(r"(?is)<(script|style).*>.*?</\1>", "", html)
    txt = re.sub(r"(?is)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?is)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?is)<.*?>", "", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt


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


def _normalize_order(item: Dict[str, Any]) -> Dict[str, Any]:
    customer = item.get("customer") or {}
    product = item.get("product") or {}
    utm = item.get("utm") or {}

    return {
        "cakto_order_id": str(_pick(item, "id", "_id", "order_id") or "").strip(),
        "cakto_product_id": str(
            _pick(product, "id", "_id", "product_id") or _pick(item, "product_id") or ""
        ).strip() or None,
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

def _get_source_event_from_payload(raw: dict | None) -> str | None:
    raw = raw or {}
    event = raw.get("event")
    if isinstance(event, dict):
        val = event.get("custom_id") or event.get("name") or event.get("id")
        return str(val).strip().lower() if val is not None else None

    for key in ("_cobrax_source_event", "custom_id", "event_type", "event", "type"):
        val = raw.get(key)
        if val is not None:
            return str(val).strip().lower()

    return None


def _is_subscription_renewed(order: CaktoOrder) -> bool:
    raw = getattr(order, "raw_payload", None) or {}
    source_event = _get_source_event_from_payload(raw)
    if source_event == "subscription_renewed":
        return True

    offer_type = str(getattr(order, "offer_type", None) or "").strip().lower()
    status = str(getattr(order, "status", None) or "").strip().lower()

    direct_flags = [
        raw.get("subscription_renewed"),
        raw.get("is_subscription_renewed"),
        raw.get("renewed"),
        raw.get("isRenewal"),
        raw.get("renewal"),
    ]
    if any(bool(v) for v in direct_flags):
        return True

    recurrence_number = raw.get("recurrence_number") or raw.get("recurrenceNumber")
    try:
        if recurrence_number is not None and int(recurrence_number) > 1:
            return True
    except Exception:
        pass

    text_blob = " ".join(
        [
            offer_type,
            status,
            str(raw.get("type") or ""),
            str(raw.get("event") or ""),
            str(raw.get("billing_type") or ""),
            str(raw.get("offer_type") or ""),
            str(raw.get("offerType") or ""),
            str(raw.get("description") or ""),
        ]
    ).lower()

    looks_subscription = any(
        token in text_blob
        for token in [
            "subscription",
            "assinatura",
            "recorr",
            "renew",
            "renov",
        ]
    )

    if looks_subscription and (
        status == "paid" or getattr(order, "paid_at", None) is not None
    ):
        return True

    return False


def _event_matches_order(automation: CaktoAutomation, order: CaktoOrder) -> bool:
    event_type = str(getattr(automation, "event_type", "order_paid") or "order_paid").strip().lower()
    raw = getattr(order, "raw_payload", None) or {}
    source_event = _get_source_event_from_payload(raw)

    if event_type == "order_created":
        if source_event:
            return source_event in {
                "initiate_checkout",
                "pix_gerado",
                "boleto_gerado",
                "picpay_gerado",
                "openfinance_nubank_gerado",
                "subscription_created",
            }
        return True

    if event_type == "order_paid":
        if source_event:
            return source_event == "purchase_approved"
        status = str(getattr(order, "status", None) or "").strip().lower()
        return status == "paid" or getattr(order, "paid_at", None) is not None

    if event_type == "order_refunded":
        if source_event:
            return source_event in {"refund", "chargeback"}
        status = str(getattr(order, "status", None) or "").strip().lower()
        return status == "refunded" or getattr(order, "refunded_at", None) is not None

    if event_type == "subscription_renewed":
        return _is_subscription_renewed(order)

    return False


def _automation_matches_product(automation: CaktoAutomation, order: CaktoOrder) -> bool:
    wanted = str(getattr(automation, "cakto_product_id", None) or "").strip()
    if not wanted:
        return True
    return str(getattr(order, "cakto_product_id", None) or "").strip() == wanted


def _filter_orders_for_automation(automation: CaktoAutomation, orders: list[CaktoOrder]) -> list[CaktoOrder]:
    out = []
    for order in orders or []:
        if not _automation_matches_product(automation, order):
            continue
        if not _event_matches_order(automation, order):
            continue
        out.append(order)
    return out


def _extract_order_id_from_webhook_payload(payload: dict) -> str | None:
    candidates = [
        payload,
        payload.get("data") if isinstance(payload.get("data"), dict) else {},
        payload.get("order") if isinstance(payload.get("order"), dict) else {},
        payload.get("purchase") if isinstance(payload.get("purchase"), dict) else {},
    ]

    for obj in candidates:
        val = _pick(obj, "order_id", "id", "_id")
        if val is not None:
            s = str(val).strip()
            if s:
                return s

    data = payload.get("data") or {}
    order = data.get("order") if isinstance(data, dict) else {}
    if isinstance(order, dict):
        val = _pick(order, "id", "_id", "order_id")
        if val is not None:
            s = str(val).strip()
            if s:
                return s

    return None


def _upsert_company_cakto_order_from_raw(
    *,
    db,
    company: Company,
    raw_item: dict,
) -> tuple[CaktoOrder, bool]:
    norm = _normalize_order(raw_item)
    external_id = norm["cakto_order_id"]
    if not external_id:
        raise RuntimeError("Payload da Cakto sem id de pedido")

    existing = (
        db.query(CaktoOrder)
        .filter(
            CaktoOrder.company_id == company.id,
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
        db.flush()
        return existing, False

    obj = CaktoOrder(
        company_id=company.id,
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
    db.flush()
    return obj, True

def _action_syncs_customer(action_type: str | None) -> bool:
    return action_type in {"sync_customer", "sync_customer_and_send_email"}


def _action_sends_email(action_type: str | None, send_email_after: bool | None = None) -> bool:
    return bool(send_email_after) or action_type in {"send_email", "sync_customer_and_send_email"}

def _sync_customers_from_orders(
    *,
    db,
    company: Company,
    company_id,
    orders: list[CaktoOrder],
):
    if not orders:
        return {
            "created": 0,
            "updated": 0,
            "skipped_no_email": 0,
            "skipped_unchanged": 0,
            "scanned_orders": 0,
            "matched_pairs": [],
        }

    created = 0
    updated = 0
    skipped_no_email = 0
    skipped_unchanged = 0
    matched_pairs = []

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

            if hasattr(existing, "source_system") and not (existing.source_system or "").strip():
                existing.source_system = "CAKTO"
                changed = True

            if hasattr(existing, "source_external_ref") and order_ref and not (existing.source_external_ref or "").strip():
                existing.source_external_ref = order_ref
                changed = True

            if hasattr(existing, "last_order_at") and order.order_created_at and (
                existing.last_order_at is None or order.order_created_at > existing.last_order_at
            ):
                existing.last_order_at = order.order_created_at
                changed = True

            if changed:
                db.add(existing)
                updated += 1
            else:
                skipped_unchanged += 1

            matched_pairs.append(
                {
                    "client_id": existing.id,
                    "order_id": order.id,
                }
            )
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
            source_system="CAKTO" if hasattr(Client, "source_system") else None,
            source_external_ref=order_ref if hasattr(Client, "source_external_ref") else None,
            last_order_at=order.order_created_at if hasattr(Client, "last_order_at") else None,
        )
        db.add(new_client)
        db.flush()

        created += 1
        matched_pairs.append(
            {
                "client_id": new_client.id,
                "order_id": order.id,
            }
        )

    db.commit()

    return {
        "created": created,
        "updated": updated,
        "skipped_no_email": skipped_no_email,
        "skipped_unchanged": skipped_unchanged,
        "scanned_orders": len(orders),
        "matched_pairs": matched_pairs,
    }


def _queue_automation_emails_from_orders(
    *,
    db,
    company: Company,
    company_id,
    template: EmailTemplate,
    orders: list[CaktoOrder],
) -> int:
    queued_log_ids: list[str] = []
    emails_queued = 0
    seen_emails: set[str] = set()

    for order in orders or []:
        to_email = (getattr(order, "customer_email", None) or "").strip().lower()
        if not to_email:
            continue

        if to_email in seen_emails:
            continue
        seen_emails.add(to_email)

        existing_client = (
            db.query(Client)
            .filter(
                Client.company_id == company_id,
                Client.email == to_email,
            )
            .first()
        )

        runtime_client = existing_client or SimpleNamespace(
            nome=(getattr(order, "customer_name", None) or to_email.split("@")[0]),
            email=to_email,
            telefone=getattr(order, "customer_phone", None),
        )

        ctx = build_default_context(
            company=company,
            client=runtime_client,
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
            client_id=existing_client.id if existing_client else None,
            template_id=template.id,
            status="PENDING",
            to_email=to_email,
            to_name=getattr(runtime_client, "nome", None),
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


def _run_company_cakto_automations(
    *,
    db,
    company: Company,
    new_order_ids: list,
) -> Dict[str, Any]:
    if not new_order_ids:
        return {
            "automations_processed": 0,
            "clients_created": 0,
            "clients_updated": 0,
            "emails_queued": 0,
        }

    automations = (
        db.query(CaktoAutomation)
        .filter(
            CaktoAutomation.company_id == company.id,
            CaktoAutomation.is_active.is_(True),
        )
        .order_by(CaktoAutomation.created_at.asc())
        .all()
    )

    if not automations:
        return {
            "automations_processed": 0,
            "clients_created": 0,
            "clients_updated": 0,
            "emails_queued": 0,
        }

    candidate_orders = (
        db.query(CaktoOrder)
        .filter(
            CaktoOrder.company_id == company.id,
            CaktoOrder.id.in_(new_order_ids),
        )
        .order_by(
            CaktoOrder.order_created_at.desc().nullslast(),
            CaktoOrder.created_at.desc(),
        )
        .all()
    )

    total_created = 0
    total_updated = 0
    total_emails = 0
    processed = 0

    for obj in automations:
        matched_orders = _filter_orders_for_automation(obj, candidate_orders)

        if _action_syncs_customer(obj.action_type):
            result = _sync_customers_from_orders(
                db=db,
                company=company,
                company_id=company.id,
                orders=matched_orders,
            )
            total_created += int(result["created"])
            total_updated += int(result["updated"])

        if _action_sends_email(obj.action_type, getattr(obj, "send_email_after", False)):
            if obj.template_id:
                tpl = (
                    db.query(EmailTemplate)
                    .filter(
                        EmailTemplate.company_id == company.id,
                        EmailTemplate.id == obj.template_id,
                    )
                    .first()
                )
                if tpl:
                    total_emails += _queue_automation_emails_from_orders(
                        db=db,
                        company=company,
                        company_id=company.id,
                        template=tpl,
                        orders=matched_orders,
                    )

        obj.last_run_at = datetime.now(timezone.utc)
        obj.updated_at = datetime.now(timezone.utc)
        db.add(obj)
        db.commit()

        processed += 1

    return {
        "automations_processed": processed,
        "clients_created": total_created,
        "clients_updated": total_updated,
        "emails_queued": total_emails,
    }


def _sync_company_cakto_pipeline(company_id: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        company: Company | None = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            return {"ok": False, "error": "Empresa não encontrada", "company_id": company_id}

        client_id = (company.cakto_client_id or "").strip()
        client_secret = (company.cakto_client_secret or "").strip()

        if not bool(getattr(company, "cakto_enabled", False)):
            return {"ok": True, "skipped": True, "reason": "cakto_disabled", "company_id": company_id}

        if not client_id or not client_secret:
            return {"ok": True, "skipped": True, "reason": "missing_credentials", "company_id": company_id}

        token_data = get_access_token(client_id=client_id, client_secret=client_secret)
        access_token = str(token_data.get("access_token") or "").strip()

        result = list_all_orders(access_token, page_size=100, max_pages=20)

        created = 0
        updated = 0
        new_order_ids: list = []

        for raw_item in result["items"]:
            norm = _normalize_order(raw_item)
            external_id = norm["cakto_order_id"]
            if not external_id:
                continue

            existing = (
                db.query(CaktoOrder)
                .filter(
                    CaktoOrder.company_id == company.id,
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
                    company_id=company.id,
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
                db.flush()
                created += 1
                new_order_ids.append(obj.id)

        company.cakto_last_sync_at = datetime.now(timezone.utc)
        db.add(company)
        db.commit()

        automation_result = _run_company_cakto_automations(
            db=db,
            company=company,
            new_order_ids=new_order_ids,
        )

        return {
            "ok": True,
            "company_id": str(company.id),
            "orders_created": int(created),
            "orders_updated": int(updated),
            "pages": int(result["pages"]),
            "new_orders_for_automation": int(len(new_order_ids)),
            "automations_processed": int(automation_result["automations_processed"]),
            "clients_created": int(automation_result["clients_created"]),
            "clients_updated": int(automation_result["clients_updated"]),
            "emails_queued": int(automation_result["emails_queued"]),
        }

    except Exception as e:
        db.rollback()
        return {
            "ok": False,
            "company_id": company_id,
            "error": str(e),
        }
    finally:
        db.close()


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def send_email_job(self, log_id: str):
    db = SessionLocal()
    try:
        log: EmailLog | None = db.query(EmailLog).filter(EmailLog.id == log_id).first()
        if not log:
            return

        if getattr(log, "cancelled_at", None) is not None or log.status == "CANCELLED":
            return

        company: Company | None = db.query(Company).filter(Company.id == log.company_id).first()
        if not company:
            log.status = "FAILED"
            log.error_message = "Company not found"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        if getattr(log, "campaign_id", None):
            camp = db.query(Campaign).filter(Campaign.id == log.campaign_id).first()
            if camp and camp.status == "paused":
                log.status = "PENDING"
                log.error_message = "Campaign paused"
                db.commit()
                _safe_update_totals_after_status_change(db, log)
                return

        plan: Plan | None = None
        if getattr(company, "plan_id", None):
            plan = db.query(Plan).filter(Plan.id == company.plan_id).first()

        if getattr(company, "smtp_paused", False):
            log.status = "PENDING"
            log.error_message = "SMTP pausado"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        if not log.to_email:
            log.status = "FAILED"
            log.error_message = "Log sem to_email"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        required = [company.smtp_host, company.smtp_port, company.from_email, company.from_name]
        if any(x is None for x in required):
            log.status = "FAILED"
            log.error_message = "SMTP config incompleta na company"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        now = datetime.now(timezone.utc)

        if not _same_utc_day(getattr(company, "emails_sent_today_at", None)):
            company.emails_sent_today = 0
            company.emails_sent_today_at = now
            db.commit()

        daily_limit = getattr(company, "daily_email_limit", None)
        if daily_limit is None and plan is not None:
            daily_limit = getattr(plan, "daily_email_limit", None)

        sent_today = getattr(company, "emails_sent_today", 0) or 0

        if daily_limit is not None and sent_today >= int(daily_limit):
            log.status = "DEFERRED"
            log.error_message = f"Limite diário atingido ({sent_today}/{daily_limit}) - reagendado para amanhã (UTC)"
            db.commit()
            _safe_update_totals_after_status_change(db, log)

            countdown = _seconds_until_next_utc_0005(now)
            raise self.retry(countdown=countdown)

        rate_per_min = getattr(company, "rate_per_min", None)
        if rate_per_min is None and plan is not None:
            rate_per_min = getattr(plan, "rate_per_min", None)
        if rate_per_min is None:
            rate_per_min = 20
        rate_per_min = int(rate_per_min)

        if getattr(log, "campaign_id", None):
            camp = db.query(Campaign).filter(Campaign.id == log.campaign_id).first()
            if camp and getattr(camp, "rate_per_min", None):
                rate_per_min = int(camp.rate_per_min)

        ok = throttle_company(str(company.id), rate_per_min, spin_seconds=8.0)
        if not ok:
            raise self.retry(countdown=3)

        log.attempt_count = (log.attempt_count or 0) + 1
        log.last_attempt_at = now
        log.status = "SENDING"
        log.error_message = None
        db.commit()
        _safe_update_totals_after_status_change(db, log)

        db.refresh(log)
        db.refresh(company)

        if getattr(log, "cancelled_at", None) is not None or log.status == "CANCELLED":
            return

        if getattr(log, "campaign_id", None):
            camp = db.query(Campaign).filter(Campaign.id == log.campaign_id).first()
            if camp and camp.status == "paused":
                log.status = "PENDING"
                log.error_message = "Campaign paused"
                db.commit()
                _safe_update_totals_after_status_change(db, log)
                return

        if getattr(company, "smtp_paused", False):
            log.status = "PENDING"
            log.error_message = "SMTP pausado"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        body = log.body_rendered or ""
        body_html = body if _looks_like_html(body) else None
        body_text = _strip_html_simple(body) if body_html else body

        attachments: list[EmailAttachment] = []

        if bool(getattr(log, "should_attach_pdf", False)):
            boleto_url = (getattr(log, "asaas_bank_slip_url", None) or "").strip()
            if boleto_url:
                try:
                    content, content_type = download_url_as_bytes(
                        boleto_url,
                        api_key=(company.asaas_api_key or "").strip() or None,
                    )
                    ct = (content_type or "").lower()

                    is_pdf = ("application/pdf" in ct) or boleto_url.lower().endswith(".pdf")
                    if content and is_pdf:
                        attachments.append(
                            EmailAttachment(
                                filename="boleto.pdf",
                                content=content,
                                content_type="application/pdf",
                            )
                        )
                    else:
                        log.error_message = f"URL do boleto não retornou PDF (content-type={content_type})"
                        db.commit()
                except Exception as e:
                    log.error_message = f"Falha ao baixar/anexar boleto: {e}"
                    db.commit()
            else:
                log.error_message = "should_attach_pdf=true mas asaas_bank_slip_url está vazio"
                db.commit()

        send_smtp_email(
            smtp_host=company.smtp_host,
            smtp_port=company.smtp_port,
            smtp_user=company.smtp_user or "",
            smtp_password=company.smtp_password or "",
            use_tls=bool(company.smtp_use_tls),
            from_email=company.from_email,
            from_name=company.from_name,
            to_email=log.to_email,
            subject=log.subject_rendered or "(sem assunto)",
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )

        company.emails_sent_today = (getattr(company, "emails_sent_today", 0) or 0) + 1
        company.emails_sent_today_at = now

        log.status = "SENT"
        log.sent_at = now
        db.commit()
        _safe_update_totals_after_status_change(db, log)

    except Retry:
        raise

    except Exception as e:
        try:
            log2 = db.query(EmailLog).filter(EmailLog.id == log_id).first()
            if log2:
                if getattr(log2, "cancelled_at", None) is not None or log2.status == "CANCELLED":
                    return

                retries_left = self.max_retries - self.request.retries
                log2.status = "RETRYING" if retries_left > 0 else "FAILED"
                log2.error_message = str(e)
                db.commit()
                _safe_update_totals_after_status_change(db, log2)
        except Exception:
            pass

        raise

    finally:
        db.close()


from app.workers.scheduler import run_due_campaigns


@celery_app.task(name="campaigns.run_due_campaigns")
def run_due_campaigns_job():
    return run_due_campaigns(batch_size=25)


@celery_app.task(name="cakto.sync_company")
def sync_cakto_company_job(company_id: str):
    return _sync_company_cakto_pipeline(company_id)

@celery_app.task(name="cakto.process_webhook_event")
def process_cakto_webhook_event_job(event_id: str):
    db = SessionLocal()
    try:
        row: CaktoWebhookEvent | None = db.query(CaktoWebhookEvent).filter(CaktoWebhookEvent.id == event_id).first()
        if not row:
            return {"ok": False, "error": "Evento webhook não encontrado", "event_id": event_id}

        if row.status == "PROCESSED":
            return {"ok": True, "skipped": True, "reason": "already_processed", "event_id": event_id}

        company: Company | None = db.query(Company).filter(Company.id == row.company_id).first()
        if not company:
            row.status = "FAILED"
            row.error_message = "Empresa não encontrada"
            db.add(row)
            db.commit()
            return {"ok": False, "error": "Empresa não encontrada", "event_id": event_id}

        row.status = "PROCESSING"
        db.add(row)
        db.commit()

        payload = row.payload or {}
        source_event = _get_source_event_from_payload(payload)
        external_order_id = _extract_order_id_from_webhook_payload(payload)

        client_id = (company.cakto_client_id or "").strip()
        client_secret = (company.cakto_client_secret or "").strip()

        if not client_id or not client_secret:
            raise RuntimeError("Credenciais da Cakto ausentes na empresa")

        token_data = get_access_token(client_id=client_id, client_secret=client_secret)
        access_token = str(token_data.get("access_token") or "").strip()

        raw_order = None
        if external_order_id:
            try:
                raw_order = retrieve_order(access_token, external_order_id)
            except Exception:
                raw_order = None

        if not isinstance(raw_order, dict) or not raw_order:
            raw_order = payload.get("data") if isinstance(payload.get("data"), dict) else payload

        if not isinstance(raw_order, dict) or not raw_order:
            row.status = "IGNORED"
            row.error_message = "Não foi possível extrair dados do pedido do payload"
            row.processed_at = datetime.now(timezone.utc)
            db.add(row)
            db.commit()
            return {"ok": True, "ignored": True, "event_id": event_id}

        raw_order["_cobrax_source_event"] = source_event

        order, created = _upsert_company_cakto_order_from_raw(
            db=db,
            company=company,
            raw_item=raw_order,
        )

        company.cakto_last_webhook_at = datetime.now(timezone.utc)
        db.add(company)
        db.commit()

        automation_result = _run_company_cakto_automations(
            db=db,
            company=company,
            new_order_ids=[order.id],
        )

        row.status = "PROCESSED"
        row.processed_at = datetime.now(timezone.utc)
        row.external_order_id = str(getattr(order, "cakto_order_id", None) or row.external_order_id or "")
        db.add(row)
        db.commit()

        return {
            "ok": True,
            "event_id": event_id,
            "order_id": str(order.id),
            "order_created": bool(created),
            "automations_processed": int(automation_result["automations_processed"]),
            "clients_created": int(automation_result["clients_created"]),
            "clients_updated": int(automation_result["clients_updated"]),
            "emails_queued": int(automation_result["emails_queued"]),
        }

    except Exception as e:
        db.rollback()
        try:
            row2 = db.query(CaktoWebhookEvent).filter(CaktoWebhookEvent.id == event_id).first()
            if row2:
                row2.status = "FAILED"
                row2.error_message = str(e)
                row2.processed_at = datetime.now(timezone.utc)
                db.add(row2)
                db.commit()
        except Exception:
            pass

        return {
            "ok": False,
            "event_id": event_id,
            "error": str(e),
        }
    finally:
        db.close()

@celery_app.task(name="cakto.sync_all_companies")
def sync_all_cakto_companies_job():
    db = SessionLocal()
    try:
        companies = (
            db.query(Company)
            .filter(Company.cakto_enabled.is_(True))
            .filter(Company.cakto_client_id.isnot(None))
            .filter(Company.cakto_client_secret.isnot(None))
            .all()
        )
        company_ids = [str(c.id) for c in companies]
    finally:
        db.close()

    results = []
    ok_count = 0
    fail_count = 0

    for company_id in company_ids:
        result = _sync_company_cakto_pipeline(company_id)
        results.append(result)
        if result.get("ok"):
            ok_count += 1
        else:
            fail_count += 1

    return {
        "ok": True,
        "companies_found": len(company_ids),
        "companies_ok": ok_count,
        "companies_failed": fail_count,
        "results": results,
    }