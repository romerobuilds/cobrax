from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database_.database import SessionLocal

from app.models.campaign import Campaign
from app.models.campaign_run import CampaignRun
from app.models.campaign_target import CampaignTarget
from app.models.client import Client
from app.models.email_template import EmailTemplate
from app.models.email_log import EmailLog

from app.workers.tasks import send_email_job


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """
    Se dt for naive, assume UTC.
    Se dt tiver tzinfo, converte pra UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _render_placeholders(text: str, ctx: Dict[str, Any]) -> str:
    if not text:
        return ""
    out = text
    for k, v in (ctx or {}).items():
        out = out.replace("{{" + str(k) + "}}", str(v))
    return out


def _start_campaign_run(db: Session, camp: Campaign) -> CampaignRun:
    """
    Inicia uma campanha:
    - cria CampaignRun
    - cria EmailLogs (QUEUED)
    - COMMIT
    - enfileira send_email_job para cada log
    """
    tpl = db.query(EmailTemplate).filter(EmailTemplate.id == camp.template_id).first()
    if not tpl:
        raise RuntimeError("Template não encontrado para campanha agendada")

    targets = db.query(CampaignTarget).filter(CampaignTarget.campaign_id == camp.id).all()
    if not targets:
        raise RuntimeError("Campanha agendada sem targets")

    run = CampaignRun(campaign_id=camp.id, status="running", totals={})
    db.add(run)

    # muda status da campanha
    camp.status = "running"

    db.flush()  # garante run.id

    created_logs = 0
    now = _utc_now()
    queued_log_ids: List[str] = []

    for t in targets:
        ctx: Dict[str, Any] = dict(camp.context or {})
        if t.payload:
            ctx.update(t.payload)

        to_email: Optional[str] = None
        to_name: Optional[str] = None

        if t.client_id:
            c = (
                db.query(Client)
                .filter(Client.id == t.client_id, Client.company_id == camp.company_id)
                .first()
            )
            if not c:
                continue

            to_email = getattr(c, "email", None)
            to_name = getattr(c, "nome", None) or getattr(c, "name", None)

            if getattr(c, "nome", None) is not None:
                ctx.setdefault("nome", c.nome)
            if getattr(c, "email", None) is not None:
                ctx.setdefault("email", c.email)
        else:
            to_email = (t.email or "").strip() if t.email else None

        if not to_email:
            continue

        subject_tpl = getattr(tpl, "assunto", None) or getattr(tpl, "subject", None) or ""
        body_tpl = (
            getattr(tpl, "html", None)
            or getattr(tpl, "body_html", None)
            or getattr(tpl, "body", None)
            or ""
        )

        subject_rendered = _render_placeholders(subject_tpl, ctx)
        body_rendered = _render_placeholders(body_tpl, ctx)

        log = EmailLog(
            company_id=camp.company_id,
            client_id=t.client_id,
            template_id=camp.template_id,
            status="QUEUED",
            to_email=to_email,
            to_name=to_name,
            subject_rendered=subject_rendered,
            body_rendered=body_rendered,
            attempt_count=0,
            created_at=now,
            campaign_id=camp.id,
            campaign_run_id=run.id,
        )
        db.add(log)
        db.flush()  # garante log.id
        queued_log_ids.append(str(log.id))
        created_logs += 1

    run.totals = {
        "total": int(created_logs),
        "sent": 0,
        "failed": 0,
        "cancelled": 0,
        "pending": int(created_logs),
        "by_status": {"QUEUED": int(created_logs)} if created_logs else {},
    }

    # ✅ commit antes de enfileirar (evita corrida do worker não achar log)
    db.commit()
    db.refresh(run)

    # ✅ enfileira após commit
    for log_id in queued_log_ids:
        send_email_job.delay(log_id)

    return run


def run_due_campaigns(batch_size: int = 25) -> dict:
    """
    Busca campanhas vencidas e inicia.
    Usa lock (FOR UPDATE SKIP LOCKED) pra evitar duplicar em concorrência.
    """
    db = SessionLocal()
    now_utc = _utc_now()

    try:
        q = (
            db.query(Campaign)
            .filter(Campaign.scheduled_at.isnot(None))
            .filter(Campaign.status == "scheduled")
            .filter(Campaign.scheduled_at <= now_utc)
            .order_by(Campaign.scheduled_at.asc())
        )

        camps = q.with_for_update(skip_locked=True).limit(batch_size).all()

        started = 0
        skipped = 0
        errors: list[str] = []

        for camp in camps:
            try:
                sched_utc = _as_utc(getattr(camp, "scheduled_at", None))
                if not sched_utc or sched_utc > now_utc:
                    skipped += 1
                    continue

                # se status mudou por alguma razão, pula
                if camp.status in ("running", "paused", "done", "cancelled"):
                    skipped += 1
                    continue

                # idempotência forte:
                # se já existe run "running" -> não duplica
                running_run = (
                    db.query(CampaignRun)
                    .filter(CampaignRun.campaign_id == camp.id)
                    .filter(CampaignRun.status == "running")
                    .order_by(CampaignRun.started_at.desc())
                    .first()
                )
                if running_run:
                    camp.status = "running"
                    db.commit()
                    skipped += 1
                    continue

                # se já existe um run mais recente e já existem logs para ele,
                # é sinal de que o start já aconteceu antes (mesmo que status esteja errado)
                last_run = (
                    db.query(CampaignRun)
                    .filter(CampaignRun.campaign_id == camp.id)
                    .order_by(CampaignRun.started_at.desc())
                    .first()
                )
                if last_run:
                    any_logs = (
                        db.query(EmailLog.id)
                        .filter(EmailLog.campaign_run_id == last_run.id)
                        .first()
                    )
                    if any_logs:
                        # corrige status da campanha baseado no run
                        if last_run.status in ("running", "paused"):
                            camp.status = last_run.status
                        elif last_run.status in ("finished", "cancelled"):
                            camp.status = "done" if last_run.status == "finished" else "cancelled"
                        db.commit()
                        skipped += 1
                        continue

                _start_campaign_run(db, camp)
                started += 1

            except Exception as e:
                errors.append(f"{camp.id}: {str(e)}")
                # deixa em ready pra você enxergar no Swagger/front e decidir
                try:
                    camp.status = "ready"
                    db.commit()
                except Exception:
                    db.rollback()

        return {"ok": True, "started": int(started), "skipped": int(skipped), "errors": errors}

    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()