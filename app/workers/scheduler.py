from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
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


PENDING_STATUSES = ["QUEUED", "PENDING", "SCHEDULED", "SENDING", "RETRYING", "DEFERRED"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
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


def _compute_next_run(c: Campaign, base_utc: datetime) -> datetime | None:
    rt = (getattr(c, "repeat_type", None) or "none").lower()
    every = int(getattr(c, "repeat_every", 0) or 0)

    if rt == "none" or every <= 0:
        return None

    if rt == "minutes":
        return base_utc + timedelta(minutes=every)
    if rt == "hours":
        return base_utc + timedelta(hours=every)
    if rt == "days":
        return base_utc + timedelta(days=every)
    if rt == "weeks":
        # weekdays "0,1,2..." (seg=0..dom=6)
        raw = getattr(c, "repeat_weekdays", None)
        weekdays: list[int] = []
        if raw:
            try:
                weekdays = [int(x) for x in raw.split(",") if x.strip() != ""]
            except Exception:
                weekdays = []
        weekdays = sorted({d for d in weekdays if 0 <= d <= 6})

        if not weekdays:
            return base_utc + timedelta(weeks=every)

        # procura o próximo dia permitido (até 14 dias)
        for i in range(1, 15):
            cand = base_utc + timedelta(days=i)
            if cand.weekday() in weekdays:
                return cand

        return base_utc + timedelta(weeks=every)

    return None


def _schedule_can_fire(c: Campaign) -> bool:
    if not bool(getattr(c, "is_schedule_enabled", False)):
        return False

    next_run_at = _as_utc(getattr(c, "next_run_at", None))
    if not next_run_at:
        return False

    end_at = _as_utc(getattr(c, "end_at", None))
    if end_at and next_run_at > end_at:
        return False

    max_occ = getattr(c, "max_occurrences", None)
    occ = int(getattr(c, "occurrences", 0) or 0)
    if max_occ is not None and occ >= int(max_occ):
        return False

    return True


def _maybe_finalize_last_run(db: Session, camp: Campaign) -> None:
    """
    Se o último run estiver "running/paused" mas não houver mais logs pendentes,
    finaliza o run e ajusta status da campanha.
    Isso é necessário pra repetição funcionar sem um finalizador no worker.
    """
    last_run = (
        db.query(CampaignRun)
        .filter(CampaignRun.campaign_id == camp.id)
        .order_by(CampaignRun.started_at.desc())
        .first()
    )
    if not last_run:
        return

    if last_run.status not in ("running", "paused"):
        return

    pending_any = (
        db.query(EmailLog.id)
        .filter(EmailLog.campaign_run_id == last_run.id)
        .filter(EmailLog.status.in_(PENDING_STATUSES))
        .first()
    )
    if pending_any:
        return

    # não há mais pendências -> finaliza
    last_run.status = "finished"
    last_run.finished_at = _utc_now()

    # campanha: se foi cancelada manualmente, respeita
    if camp.status not in ("cancelled",):
        camp.status = "done"

    db.add(last_run)
    db.add(camp)
    db.commit()


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
        raise RuntimeError("Template não encontrado para campanha")

    targets = db.query(CampaignTarget).filter(CampaignTarget.campaign_id == camp.id).all()
    if not targets:
        raise RuntimeError("Campanha sem targets")

    run = CampaignRun(campaign_id=camp.id, status="running", totals={})
    db.add(run)

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
        body_tpl = getattr(tpl, "corpo_html", None) or getattr(tpl, "html", None) or getattr(tpl, "body_html", None) or getattr(tpl, "body", None) or ""

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
        db.flush()
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

    # commit antes de enfileirar
    db.commit()
    db.refresh(run)

    for log_id in queued_log_ids:
        send_email_job.delay(log_id)

    return run


def _run_due_legacy_scheduled(db: Session, now_utc: datetime, batch_size: int) -> tuple[int, int, list[str]]:
    """
    Legacy: scheduled_at + status='scheduled'
    """
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

            if camp.status in ("running", "paused", "done", "cancelled"):
                skipped += 1
                continue

            # se já existe run "running", não duplica
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

            _start_campaign_run(db, camp)
            started += 1

        except Exception as e:
            errors.append(f"{camp.id}: {str(e)}")
            try:
                camp.status = "ready"
                db.commit()
            except Exception:
                db.rollback()

    return started, skipped, errors


def _run_due_recurring(db: Session, now_utc: datetime, batch_size: int) -> tuple[int, int, list[str]]:
    """
    Novo: is_schedule_enabled + next_run_at
    """
    q = (
        db.query(Campaign)
        .filter(Campaign.is_schedule_enabled == True)  # noqa: E712
        .filter(Campaign.next_run_at.isnot(None))
        .filter(Campaign.next_run_at <= now_utc)
        .order_by(Campaign.next_run_at.asc())
    )

    camps = q.with_for_update(skip_locked=True).limit(batch_size).all()

    started = 0
    skipped = 0
    errors: list[str] = []

    for camp in camps:
        try:
            # tenta finalizar run anterior se já acabou
            _maybe_finalize_last_run(db, camp)

            if not _schedule_can_fire(camp):
                camp.is_schedule_enabled = False
                db.add(camp)
                db.commit()
                skipped += 1
                continue

            next_run_utc = _as_utc(getattr(camp, "next_run_at", None))
            if not next_run_utc or next_run_utc > now_utc:
                skipped += 1
                continue

            # não dispara se ainda tem run ativo com pendências
            last_run = (
                db.query(CampaignRun)
                .filter(CampaignRun.campaign_id == camp.id)
                .order_by(CampaignRun.started_at.desc())
                .first()
            )
            if last_run and last_run.status in ("running", "paused"):
                pending_any = (
                    db.query(EmailLog.id)
                    .filter(EmailLog.campaign_run_id == last_run.id)
                    .filter(EmailLog.status.in_(PENDING_STATUSES))
                    .first()
                )
                if pending_any:
                    # empurra um pouco pra frente pra não ficar “travando” no vencido
                    camp.next_run_at = now_utc + timedelta(seconds=30)
                    db.add(camp)
                    db.commit()
                    skipped += 1
                    continue

            # dispara
            _start_campaign_run(db, camp)
            started += 1

            # atualiza ocorrências
            camp.occurrences = int(getattr(camp, "occurrences", 0) or 0) + 1

            # calcula próximo
            nxt = _compute_next_run(camp, base_utc=now_utc)
            camp.next_run_at = nxt

            # se não tem próximo, desativa
            if nxt is None:
                camp.is_schedule_enabled = False

            db.add(camp)
            db.commit()

        except Exception as e:
            errors.append(f"{camp.id}: {str(e)}")
            try:
                # não desabilita automaticamente — deixa visível
                db.commit()
            except Exception:
                db.rollback()

    return started, skipped, errors


def run_due_campaigns(batch_size: int = 25) -> dict:
    """
    Roda os dois:
    - legacy scheduled_at
    - novo recurring next_run_at
    """
    db = SessionLocal()
    now_utc = _utc_now()

    try:
        started_legacy, skipped_legacy, errors_legacy = _run_due_legacy_scheduled(db, now_utc, batch_size)
        started_new, skipped_new, errors_new = _run_due_recurring(db, now_utc, batch_size)

        return {
            "ok": True,
            "now_utc": now_utc.isoformat(),
            "legacy": {"started": int(started_legacy), "skipped": int(skipped_legacy), "errors": errors_legacy},
            "recurring": {"started": int(started_new), "skipped": int(skipped_new), "errors": errors_new},
        }

    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


def main_loop(interval_seconds: int = 10):
    print("[scheduler] started loop, interval:", interval_seconds)
    while True:
        out = run_due_campaigns(batch_size=25)
        if not out.get("ok"):
            print("[scheduler] ERROR:", out)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main_loop(10)