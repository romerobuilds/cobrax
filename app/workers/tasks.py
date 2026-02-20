# app/workers/tasks.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import func
from celery.exceptions import Retry  # ✅ importante: não tratar self.retry como "erro"

from app.workers.celery_app import celery_app
from app.database_.database import SessionLocal
from app.models.email_log import EmailLog
from app.models.company import Company
from app.models.plan import Plan
from app.services.mailer import send_smtp_email
from app.workers.rate_limiter import throttle_company

# Campanhas
from app.models.campaign import Campaign
from app.models.campaign_run import CampaignRun


def _same_utc_day(dt) -> bool:
    """Retorna True se dt está no mesmo dia (UTC) do 'agora'."""
    if not dt:
        return False
    return dt.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def _seconds_until_next_utc_0005(now_utc: datetime) -> int:
    """
    Calcula quantos segundos faltam até 00:05 UTC do próximo dia.
    Usa mínimo de 60s para evitar retry imediato.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    next_day_date = now_utc.date() + timedelta(days=1)
    next_run = (
        datetime.combine(next_day_date, datetime.min.time(), tzinfo=timezone.utc)
        + timedelta(minutes=5)
    )
    seconds = int((next_run - now_utc).total_seconds())
    return max(60, seconds)


def _render_placeholders(text: str, ctx: dict) -> str:
    """Render simples: troca {{chave}} por valor."""
    if not text:
        return ""
    out = text
    for k, v in (ctx or {}).items():
        out = out.replace("{{" + str(k) + "}}", str(v))
    return out


def _recompute_run_totals(db, run_id: str):
    """
    Recalcula totals do run com base em email_logs.
    Inclui CANCELLED e calcula total como soma real dos status.
    Finaliza run/campanha quando pending_like == 0.
    """
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

    # ✅ finaliza automaticamente quando não tiver pendentes
    if run.status in ("running", "paused") and pending_like == 0:
        # se já estiver cancelado no nível do run, respeita
        if run.status != "cancelled":
            run.status = "finished"
        run.finished_at = datetime.now(timezone.utc)

        camp = db.query(Campaign).filter(Campaign.id == run.campaign_id).first()
        if camp and camp.status not in ("cancelled",):
            # mantém seu padrão existente
            camp.status = "done"

    db.commit()


def _safe_update_totals_after_status_change(db, log: EmailLog | None):
    """Atualiza totals se o log pertence a um run."""
    if not log:
        return
    if getattr(log, "campaign_run_id", None):
        _recompute_run_totals(db, str(log.campaign_run_id))


@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,      # 1m, 2m, 4m...
    retry_backoff_max=300,   # máx 5 min
    retry_jitter=True,
)
def send_email_job(self, log_id: str):
    db = SessionLocal()
    try:
        log: EmailLog | None = db.query(EmailLog).filter(EmailLog.id == log_id).first()
        if not log:
            return

        # ✅ cancelado? ignora
        if getattr(log, "cancelled_at", None) is not None or log.status == "CANCELLED":
            return

        company: Company | None = db.query(Company).filter(Company.id == log.company_id).first()
        if not company:
            log.status = "FAILED"
            log.error_message = "Company not found"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        # ✅ verifica campanha pausada (ANTES)
        if getattr(log, "campaign_id", None):
            camp = db.query(Campaign).filter(Campaign.id == log.campaign_id).first()
            if camp and camp.status == "paused":
                log.status = "PENDING"
                log.error_message = "Campaign paused"
                db.commit()
                _safe_update_totals_after_status_change(db, log)
                return

        # ✅ carrega plano (se houver)
        plan: Plan | None = None
        if getattr(company, "plan_id", None):
            plan = db.query(Plan).filter(Plan.id == company.plan_id).first()

        # ✅ SMTP pausado: não é erro, deixa pendente
        if getattr(company, "smtp_paused", False):
            log.status = "PENDING"
            log.error_message = "SMTP pausado"
            db.commit()
            _safe_update_totals_after_status_change(db, log)
            return

        # ✅ validações mínimas
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

        # ✅ reset contador diário (UTC)
        if not _same_utc_day(getattr(company, "emails_sent_today_at", None)):
            company.emails_sent_today = 0
            company.emails_sent_today_at = now
            db.commit()

        # ✅ daily_limit: company override > plan > None (None = ilimitado)
        daily_limit = getattr(company, "daily_email_limit", None)
        if daily_limit is None and plan is not None:
            daily_limit = getattr(plan, "daily_email_limit", None)

        sent_today = getattr(company, "emails_sent_today", 0) or 0

        # ✅ bateu limite diário? NÃO FAIL. Marca DEFERRED e reagenda
        if daily_limit is not None and sent_today >= int(daily_limit):
            log.status = "DEFERRED"
            log.error_message = (
                f"Limite diário atingido ({sent_today}/{daily_limit}) - reagendado para amanhã (UTC)"
            )
            db.commit()
            _safe_update_totals_after_status_change(db, log)

            countdown = _seconds_until_next_utc_0005(now)
            raise self.retry(countdown=countdown)

        # ✅ rate_per_min: company override > plan > default
        rate_per_min = getattr(company, "rate_per_min", None)
        if rate_per_min is None and plan is not None:
            rate_per_min = getattr(plan, "rate_per_min", None)
        if rate_per_min is None:
            rate_per_min = 20
        rate_per_min = int(rate_per_min)

        # ✅ campaign override de rate_per_min (se existir)
        if getattr(log, "campaign_id", None):
            camp = db.query(Campaign).filter(Campaign.id == log.campaign_id).first()
            if camp and getattr(camp, "rate_per_min", None):
                rate_per_min = int(camp.rate_per_min)

        ok = throttle_company(str(company.id), rate_per_min, spin_seconds=8.0)
        if not ok:
            # não liberou dentro do spin -> reenqueue rápido
            raise self.retry(countdown=3)

        # ✅ marca tentativa
        log.attempt_count = (log.attempt_count or 0) + 1
        log.last_attempt_at = now
        log.status = "SENDING"
        log.error_message = None
        db.commit()
        _safe_update_totals_after_status_change(db, log)

        # ✅ checagem final (cancel/pause entre throttle e envio)
        db.refresh(log)
        db.refresh(company)

        if getattr(log, "cancelled_at", None) is not None or log.status == "CANCELLED":
            return

        # ✅ RECHECA campanha pausada (DEPOIS)
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

        # ✅ envia
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
            body_text=log.body_rendered or "",
        )

        # ✅ sucesso: incrementa contador diário
        company.emails_sent_today = (getattr(company, "emails_sent_today", 0) or 0) + 1
        company.emails_sent_today_at = now

        log.status = "SENT"
        log.sent_at = now
        log.error_message = None
        db.commit()
        _safe_update_totals_after_status_change(db, log)

    except Retry:
        # ✅ self.retry() NÃO é erro: não sobrescrever status (DEFERRED etc)
        raise

    except Exception as e:
        # ⚠️ Se estiver cancelado, não marca nada
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


# =========================
# SCHEDULER (Celery Beat)
# =========================

from app.workers.scheduler import run_due_campaigns


@celery_app.task(name="campaigns.run_due_campaigns")
def run_due_campaigns_job():
    return run_due_campaigns(batch_size=25)