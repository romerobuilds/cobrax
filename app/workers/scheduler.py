# app/workers/scheduler.py
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

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
    """
    base_utc = referência para calcular o próximo (ideal: o "fire time" anterior)
    repeat_type suportados: none | minutes | hours | days | weeks
    repeat_weekdays: "0,1,2" usando Python weekday (0=Mon .. 6=Sun) quando weeks
    """
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
        raw = getattr(c, "repeat_weekdays", None)
        weekdays: list[int] = []
        if raw:
            try:
                weekdays = [int(x) for x in raw.split(",") if x.strip() != ""]
            except Exception:
                weekdays = []
        weekdays = sorted({d for d in weekdays if 0 <= d <= 6})

        # se não definiu dias, cai no simples (a cada X semanas)
        if not weekdays:
            return base_utc + timedelta(weeks=every)

        # pega o próximo dia permitido, procurando até 14 dias
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


def _has_pending_logs_for_run(db: Session, run_id) -> bool:
    pending_any = (
        db.query(EmailLog.id)
        .filter(EmailLog.campaign_run_id == run_id)
        .filter(EmailLog.status.in_(PENDING_STATUSES))
        .first()
    )
    return bool(pending_any)


def _maybe_finalize_last_run(db: Session, camp: Campaign) -> None:
    """
    Se o último run estiver "running/paused" mas não houver mais logs pendentes,
    finaliza o run e ajusta status da campanha:
    - se schedule_enabled ainda ligado -> status vira "scheduled"
    - senão -> "done"
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

    if _has_pending_logs_for_run(db, last_run.id):
        return

    last_run.status = "finished"
    last_run.finished_at = _utc_now()

    if camp.status == "cancelled":
        pass
    else:
        if bool(getattr(camp, "is_schedule_enabled", False)) and _as_utc(getattr(camp, "next_run_at", None)):
            camp.status = "scheduled"
        else:
            camp.status = "done"

    db.add(last_run)
    db.add(camp)
    db.commit()


def _find_existing_run_by_fire_at(db: Session, camp_id, fire_at_utc: datetime) -> Optional[CampaignRun]:
    """
    Idempotência forte: se já existe run para (campaign_id, scheduled_fire_at=fire_at),
    não cria outro.
    """
    return (
        db.query(CampaignRun)
        .filter(CampaignRun.campaign_id == camp_id)
        .filter(CampaignRun.scheduled_fire_at == fire_at_utc)
        .order_by(CampaignRun.started_at.desc())
        .first()
    )


def _start_campaign_run(db: Session, camp: Campaign, fire_at_utc: Optional[datetime], triggered_by: str) -> CampaignRun:
    """
    Inicia uma campanha (idempotente quando fire_at_utc é informado):
    - se já existe run com scheduled_fire_at=fire_at_utc, retorna ele (não duplica)
    - senão cria:
      - CampaignRun
      - EmailLogs (QUEUED)
      - COMMIT
      - enfileira send_email_job
    """
    fire_at_utc = _as_utc(fire_at_utc)

    if fire_at_utc is not None:
        existing = _find_existing_run_by_fire_at(db, camp.id, fire_at_utc)
        if existing:
            # garante status coerente
            if camp.status not in ("cancelled",):
                camp.status = "running"
                db.add(camp)
                db.commit()
            return existing

    tpl = db.query(EmailTemplate).filter(EmailTemplate.id == camp.template_id).first()
    if not tpl:
        raise RuntimeError("Template não encontrado para campanha")

    targets = db.query(CampaignTarget).filter(CampaignTarget.campaign_id == camp.id).all()
    if not targets:
        raise RuntimeError("Campanha sem targets")

    run = CampaignRun(
        campaign_id=camp.id,
        status="running",
        totals={},
        scheduled_fire_at=fire_at_utc,
        triggered_by=(triggered_by or "manual"),
    )
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
        body_tpl = (
            getattr(tpl, "corpo_html", None)
            or getattr(tpl, "html", None)
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


def _run_due_legacy_scheduled(db: Session, now_utc: datetime, batch_size: int) -> Tuple[int, int, List[str]]:
    """
    Legacy: scheduled_at + status='scheduled'
    Idempotência: scheduled_fire_at = scheduled_at (UTC)
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
    errors: List[str] = []

    for camp in camps:
        try:
            fire_at = _as_utc(getattr(camp, "scheduled_at", None))
            if not fire_at or fire_at > now_utc:
                skipped += 1
                continue

            if camp.status in ("running", "paused", "done", "cancelled"):
                skipped += 1
                continue

            existing = _find_existing_run_by_fire_at(db, camp.id, fire_at)
            if existing:
                skipped += 1
                continue

            _start_campaign_run(db, camp, fire_at_utc=fire_at, triggered_by="scheduled_at")
            started += 1

        except Exception as e:
            errors.append(f"{camp.id}: {str(e)}")
            try:
                camp.status = "ready"
                db.commit()
            except Exception:
                db.rollback()

    return started, skipped, errors


def _run_due_recurring(db: Session, now_utc: datetime, batch_size: int) -> Tuple[int, int, List[str]]:
    """
    Novo: is_schedule_enabled + next_run_at
    Idempotência: scheduled_fire_at = next_run_at (UTC)
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
    errors: List[str] = []

    for camp in camps:
        try:
            _maybe_finalize_last_run(db, camp)

            if not _schedule_can_fire(camp):
                camp.is_schedule_enabled = False
                camp.next_run_at = None
                if camp.status not in ("cancelled",):
                    camp.status = "done"
                db.add(camp)
                db.commit()
                skipped += 1
                continue

            fire_at = _as_utc(getattr(camp, "next_run_at", None))
            if not fire_at or fire_at > now_utc:
                skipped += 1
                continue

            existing = _find_existing_run_by_fire_at(db, camp.id, fire_at)
            if existing:
                # já foi disparado para esse fire time — não duplica
                skipped += 1
                continue

            # dispara
            _start_campaign_run(db, camp, fire_at_utc=fire_at, triggered_by="recurring")
            started += 1

            # atualiza ocorrências
            camp.occurrences = int(getattr(camp, "occurrences", 0) or 0) + 1

            # calcula próximo baseado no fire_at (sem drift)
            nxt = _compute_next_run(camp, base_utc=fire_at)

            # respeita end/max
            end_at = _as_utc(getattr(camp, "end_at", None))
            max_occ = getattr(camp, "max_occurrences", None)

            if end_at and nxt and nxt > end_at:
                nxt = None
            if max_occ is not None and camp.occurrences >= int(max_occ):
                nxt = None

            camp.next_run_at = nxt

            if nxt is None:
                camp.is_schedule_enabled = False
                if camp.status not in ("cancelled",):
                    camp.status = "done"
            else:
                # fica "running" durante execução; quando acabar, tasks.py põe "scheduled"
                if camp.status not in ("cancelled",):
                    camp.status = "running"

            db.add(camp)
            db.commit()

        except Exception as e:
            errors.append(f"{camp.id}: {str(e)}")
            try:
                db.rollback()
            except Exception:
                pass

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