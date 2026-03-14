# app/workers/tasks.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re

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

from app.workers.rate_limiter import throttle_company
from app.services.mailer import send_smtp_email, EmailAttachment
from app.services.asaas_client import download_url_as_bytes


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
    txt = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    txt = re.sub(r"(?is)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?is)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?is)<.*?>", "", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt


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
                    content, content_type = download_url_as_bytes(boleto_url)
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