# app/routers/campaign.py
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database_.database import SessionLocal
from app.schemas.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignOut,
    CampaignTargetAddSelected,
    CampaignTargetAddEmails,
    CampaignRunOut,
)

from app.models.campaign import Campaign
from app.models.campaign_run import CampaignRun
from app.models.campaign_target import CampaignTarget
from app.models.client import Client
from app.models.email_template import EmailTemplate
from app.models.email_log import EmailLog

from app.workers.tasks import send_email_job

# Reuso do parser CSV/XLSX (o mesmo da fase B)
from app.services.upload_parser import parse_upload_file, normalize_header


router = APIRouter(prefix="/empresas/{company_id}/campanhas", tags=["Campanhas"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------
# Helpers
# -------------------------

def _render_placeholders(text: str, ctx: Dict[str, Any]) -> str:
    if not text:
        return ""
    out = text
    for k, v in (ctx or {}).items():
        out = out.replace("{{" + str(k) + "}}", str(v))
    return out


def _ensure_campaign_company(db: Session, company_id: str, campaign_id: str) -> Campaign:
    camp = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.company_id == company_id)
        .first()
    )
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return camp


def _ensure_template_exists(db: Session, company_id: str, template_id: str) -> EmailTemplate:
    tpl = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not tpl:
        raise HTTPException(status_code=400, detail="template_id inválido (não existe ou não pertence à empresa)")
    return tpl


def _calc_by_status_for_query(db: Session, q) -> Dict[str, int]:
    rows = q.group_by(EmailLog.status).all()
    return {str(status): int(count) for status, count in rows}


def _normalize_stats(by_status: Dict[str, int]) -> Dict[str, Any]:
    total = int(sum(by_status.values()))
    sent = int(by_status.get("SENT", 0))
    failed = int(by_status.get("FAILED", 0))
    cancelled = int(by_status.get("CANCELLED", 0))

    pending_like = 0
    for k in ["PENDING", "QUEUED", "SCHEDULED", "SENDING", "RETRYING", "DEFERRED"]:
        pending_like += int(by_status.get(k, 0))

    return {
        "total": total,
        "sent": sent,
        "failed": failed,
        "cancelled": cancelled,
        "pending": int(pending_like),
        "by_status": by_status,
    }


def _get_latest_run(db: Session, campaign_id: str) -> Optional[CampaignRun]:
    return (
        db.query(CampaignRun)
        .filter(CampaignRun.campaign_id == campaign_id)
        .order_by(CampaignRun.started_at.desc())
        .first()
    )


def _recalc_run_stats(db: Session, run_id: str) -> Dict[str, Any]:
    by_status = _calc_by_status_for_query(
        db,
        db.query(EmailLog.status, func.count(EmailLog.id)).filter(EmailLog.campaign_run_id == run_id),
    )
    return _normalize_stats(by_status)


def _normalize_var_key(h: str) -> str:
    """
    Converte header em chave amigável pra {{variavel}}:
    - trim + remove BOM
    - lower
    - espaços -> _
    - remove chars estranhos
    """
    s = normalize_header(h)
    s = s.strip().lower()
    s = s.replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s


# ======================================================
# PREVIEW (Wizard de Campanhas - Fase C)
# ======================================================

@router.post("/preview-upload", operation_id="campaigns_preview_upload")
async def preview_upload_file(
    company_id: str,
    file: UploadFile = File(...),
    email_column: str = Query(default="email", description="Nome da coluna do e-mail (ex: email)"),
    limit: int = Query(default=5000, ge=1, le=50000, description="Máximo de linhas lidas"),
):
    """
    Lê CSV/XLSX e devolve:
    - headers detectados
    - amostra (primeiras 10 linhas)
    - variáveis sugeridas (todas as colunas exceto email)
    - contadores (total lidas, sem email)
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    try:
        rows = parse_upload_file(file.filename or "", raw, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="Nenhuma linha válida encontrada")

    # headers (união das keys das primeiras linhas)
    headers_set = set()
    for r in rows[:50]:
        headers_set.update([normalize_header(k) for k in (r or {}).keys() if k])

    headers = sorted([h for h in headers_set if h])

    email_col = (email_column or "email").strip()
    email_col_l = email_col.lower()

    without_email = 0
    normalized_rows: List[Dict[str, Any]] = []
    for r in rows:
        # normaliza chaves para variáveis
        nr: Dict[str, Any] = {}
        for k, v in (r or {}).items():
            nk = _normalize_var_key(k)
            if not nk:
                continue
            nr[nk] = (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))

        # checa email (case-insensitive)
        email_val = None
        if email_col in r:
            email_val = r.get(email_col)
        else:
            for k in r.keys():
                if (k or "").strip().lower() == email_col_l:
                    email_val = r.get(k)
                    break

        email_str = (email_val or "").strip() if isinstance(email_val, str) else ("" if email_val is None else str(email_val).strip())
        if not email_str:
            without_email += 1

        normalized_rows.append(nr)

    # variáveis sugeridas: headers normalizados exceto email
    vars_suggested = sorted(
        {k for r in normalized_rows[:200] for k in r.keys()} - {email_col_l}
    )

    sample = normalized_rows[:10]

    return {
        "ok": True,
        "filename": file.filename,
        "total_rows": int(len(rows)),
        "rows_missing_email": int(without_email),
        "headers_detected": headers,
        "variables_suggested": vars_suggested,
        "sample_rows": sample,
        "note": "variables_suggested são as chaves que você pode usar no template via {{chave}} (exceto email).",
    }


@router.post("/preview-render", operation_id="campaigns_preview_render")
def preview_render(
    company_id: str,
    template_id: str = Query(..., description="ID do template da empresa"),
    context: Dict[str, Any] = None,
    row: Dict[str, Any] = None,
    db: Session = Depends(get_db),
):
    """
    Renderiza assunto/corpo do template combinando:
    - context (da campanha)
    - row (linha da planilha / payload do target)
    Retorna subject_rendered/body_rendered e as variáveis usadas.
    """
    tpl = _ensure_template_exists(db, company_id, template_id)

    ctx: Dict[str, Any] = dict(context or {})
    # normaliza row keys para {{variavel}}
    for k, v in (row or {}).items():
        nk = _normalize_var_key(k)
        if not nk:
            continue
        ctx[nk] = v

    subject_rendered = _render_placeholders(tpl.assunto or "", ctx)
    body_rendered = _render_placeholders(tpl.corpo_html or "", ctx)

    return {
        "ok": True,
        "template_id": str(tpl.id),
        "used_context": ctx,
        "subject_rendered": subject_rendered,
        "body_rendered": body_rendered,
    }


# ======================================================
# RUNS (coloca antes de "/{campaign_id}" pra evitar conflito)
# ======================================================

@router.get("/runs/{run_id}", response_model=CampaignRunOut, operation_id="campaigns_runs_get")
def get_run(company_id: str, run_id: str, db: Session = Depends(get_db)):
    run = (
        db.query(CampaignRun)
        .join(Campaign, Campaign.id == CampaignRun.campaign_id)
        .filter(Campaign.company_id == company_id)
        .filter(CampaignRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/stats", operation_id="campaigns_runs_stats")
def get_run_stats(company_id: str, run_id: str, db: Session = Depends(get_db)):
    run = (
        db.query(CampaignRun)
        .join(Campaign, Campaign.id == CampaignRun.campaign_id)
        .filter(Campaign.company_id == company_id)
        .filter(CampaignRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    stats = _recalc_run_stats(db, str(run.id))

    # persiste no totals (bom pro dashboard)
    run.totals = {
        "total": stats["total"],
        "sent": stats["sent"],
        "failed": stats["failed"],
        "cancelled": stats["cancelled"],
        "pending": stats["pending"],
        "by_status": stats["by_status"],
    }
    db.commit()
    db.refresh(run)

    out = dict(stats)
    out.update({"run_id": str(run.id), "campaign_id": str(run.campaign_id), "run_status": run.status})
    return out


# =========================
# CRUD
# =========================

@router.get("/", response_model=List[CampaignOut], operation_id="campaigns_list")
def list_campaigns(company_id: str, db: Session = Depends(get_db)):
    items = (
        db.query(Campaign)
        .filter(Campaign.company_id == company_id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return items


@router.post("/", response_model=CampaignOut, operation_id="campaigns_create")
def create_campaign(company_id: str, body: CampaignCreate, db: Session = Depends(get_db)):
    _ensure_template_exists(db, company_id, str(body.template_id))

    initial_status = "scheduled" if body.scheduled_at is not None else "draft"

    camp = Campaign(
        company_id=company_id,
        name=body.name,
        template_id=body.template_id,
        status=initial_status,
        mode=body.mode or "selected",
        context=body.context or {},
        rate_per_min=int(body.rate_per_min or 15),
        scheduled_at=body.scheduled_at,
    )
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return camp


@router.get("/{campaign_id}", response_model=CampaignOut, operation_id="campaigns_get")
def get_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    return _ensure_campaign_company(db, company_id, campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignOut, operation_id="campaigns_update")
def update_campaign(company_id: str, campaign_id: str, body: CampaignUpdate, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)
    data = body.model_dump(exclude_unset=True)

    immutable_when_running = {"running", "paused", "done", "cancelled"}
    if camp.status in immutable_when_running:
        forbidden = {"template_id", "mode", "context", "rate_per_min", "scheduled_at", "name"}
        touching = forbidden.intersection(set(data.keys()))
        if touching:
            raise HTTPException(
                status_code=400,
                detail=f"Não pode alterar {sorted(list(touching))} com status={camp.status}",
            )

    if "template_id" in data:
        _ensure_template_exists(db, company_id, str(data["template_id"]))

    scheduled_changed = "scheduled_at" in data
    new_scheduled_at = data.get("scheduled_at", None)

    for k, v in data.items():
        setattr(camp, k, v)

    if scheduled_changed:
        if new_scheduled_at is not None:
            if camp.status in ("draft", "ready"):
                camp.status = "scheduled"
        else:
            if camp.status == "scheduled":
                camp.status = "draft"

    db.commit()
    db.refresh(camp)
    return camp


@router.delete("/{campaign_id}", operation_id="campaigns_delete")
def delete_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)
    if camp.status in ("running", "paused"):
        raise HTTPException(status_code=400, detail="Não pode deletar campanha em execução/pausada")
    db.delete(camp)
    db.commit()
    return {"ok": True}


# =========================
# TARGETS
# =========================

@router.post("/{campaign_id}/targets/selected", operation_id="campaigns_targets_selected_add")
def add_targets_selected(
    company_id: str,
    campaign_id: str,
    body: CampaignTargetAddSelected,
    db: Session = Depends(get_db),
):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    added = 0
    for cid in body.client_ids or []:
        c = db.query(Client).filter(Client.id == cid, Client.company_id == company_id).first()
        if not c:
            continue

        exists = (
            db.query(CampaignTarget)
            .filter(CampaignTarget.campaign_id == camp.id, CampaignTarget.client_id == cid)
            .first()
        )
        if exists:
            continue

        t = CampaignTarget(
            campaign_id=camp.id,
            client_id=cid,
            email=None,
            payload=body.payload or {},
        )
        db.add(t)
        added += 1

    db.commit()
    return {"ok": True, "added": int(added)}


@router.post("/{campaign_id}/targets/all", operation_id="campaigns_targets_all_add")
def add_targets_all(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    clients = db.query(Client).filter(Client.company_id == company_id).all()

    added = 0
    for c in clients:
        exists = (
            db.query(CampaignTarget)
            .filter(CampaignTarget.campaign_id == camp.id, CampaignTarget.client_id == c.id)
            .first()
        )
        if exists:
            continue

        t = CampaignTarget(
            campaign_id=camp.id,
            client_id=c.id,
            email=None,
            payload={},
        )
        db.add(t)
        added += 1

    db.commit()
    return {"ok": True, "added": int(added)}


@router.post("/{campaign_id}/targets/emails", operation_id="campaigns_targets_emails_add")
def add_targets_emails(
    company_id: str,
    campaign_id: str,
    body: CampaignTargetAddEmails,
    db: Session = Depends(get_db),
):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    added = 0
    for email in body.emails or []:
        e = (email or "").strip()
        if not e:
            continue

        exists = (
            db.query(CampaignTarget)
            .filter(CampaignTarget.campaign_id == camp.id)
            .filter(func.lower(CampaignTarget.email) == func.lower(e))
            .first()
        )
        if exists:
            continue

        t = CampaignTarget(
            campaign_id=camp.id,
            client_id=None,
            email=e,
            payload=body.payload or {},
        )
        db.add(t)
        added += 1

    db.commit()
    return {"ok": True, "added": int(added)}


@router.post("/{campaign_id}/targets/upload", operation_id="campaigns_targets_upload")
async def upload_targets_file(
    company_id: str,
    campaign_id: str,
    file: UploadFile = File(...),
    email_column: str = Query(default="email", description="Nome da coluna do e-mail (ex: email)"),
    name_column: str = Query(default="nome", description="Nome da coluna do nome (ex: nome)"),
    limit: int = Query(default=5000, ge=1, le=50000, description="Máximo de linhas lidas"),
    db: Session = Depends(get_db),
):
    """
    Upload CSV/XLSX:
    - A coluna `email_column` vira destination (CampaignTarget.email)
    - As demais colunas viram `payload` (variáveis do template: {{coluna}})
    """
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    try:
        rows = parse_upload_file(file.filename or "", raw, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="Nenhuma linha válida encontrada")

    email_col = (email_column or "email").strip()
    name_col = (name_column or "nome").strip()

    existing_emails = set(
        e.lower()
        for (e,) in db.query(CampaignTarget.email)
        .filter(CampaignTarget.campaign_id == camp.id)
        .filter(CampaignTarget.email.isnot(None))
        .all()
        if e
    )

    added = 0
    skipped_no_email = 0
    skipped_duplicate = 0
    skipped_invalid = 0
    errors: List[str] = []

    for idx, row in enumerate(rows, start=1):
        # email case-insensitive
        email_val = None
        if email_col in row:
            email_val = row.get(email_col)
        else:
            for k in row.keys():
                if (k or "").strip().lower() == email_col.strip().lower():
                    email_val = row.get(k)
                    break

        email_str = (email_val or "").strip() if isinstance(email_val, str) else ("" if email_val is None else str(email_val).strip())
        if not email_str:
            skipped_no_email += 1
            continue

        email_key = email_str.lower()
        if email_key in existing_emails:
            skipped_duplicate += 1
            continue

        payload: Dict[str, Any] = {}
        for k, v in (row or {}).items():
            if not k:
                continue
            kl = (k or "").strip().lower()
            if kl == email_col.strip().lower():
                continue

            # coluna de nome -> payload["nome"]
            if name_col and kl == name_col.strip().lower():
                payload.setdefault("nome", (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v)))
                continue

            payload[_normalize_var_key(k)] = (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))

        t = CampaignTarget(
            campaign_id=camp.id,
            client_id=None,
            email=email_str,
            payload=payload or {},
        )

        db.add(t)
        try:
            db.flush()
            existing_emails.add(email_key)
            added += 1
        except IntegrityError:
            db.rollback()
            skipped_duplicate += 1
        except Exception as e:
            db.rollback()
            skipped_invalid += 1
            errors.append(f"linha {idx}: {str(e)}")

    if added > 0 and camp.mode != "upload":
        camp.mode = "upload"

    db.commit()

    return {
        "ok": True,
        "added": int(added),
        "skipped_no_email": int(skipped_no_email),
        "skipped_duplicate": int(skipped_duplicate),
        "skipped_invalid": int(skipped_invalid),
        "errors": errors[:20],
        "note": "As colunas (exceto email) viram variáveis do template via {{coluna}}.",
    }


# =========================
# RUN
# =========================

@router.post("/{campaign_id}/run", response_model=CampaignRunOut, operation_id="campaigns_run_start")
def start_campaign_run(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    if camp.status not in ("draft", "ready", "scheduled"):
        raise HTTPException(
            status_code=400,
            detail=f"Campanha em status inválido para iniciar: {camp.status}",
        )

    tpl = _ensure_template_exists(db, company_id, str(camp.template_id))

    targets = db.query(CampaignTarget).filter(CampaignTarget.campaign_id == camp.id).all()
    if not targets:
        raise HTTPException(status_code=400, detail="Campanha sem targets")

    run = CampaignRun(campaign_id=camp.id, status="running", totals={})
    db.add(run)
    camp.status = "running"
    db.commit()
    db.refresh(run)
    db.refresh(camp)

    created_logs = 0
    now = datetime.now(timezone.utc)
    queued_log_ids: List[str] = []

    for t in targets:
        ctx: Dict[str, Any] = dict(camp.context or {})
        if t.payload:
            ctx.update(t.payload)

        to_email = None
        to_name = None

        if t.client_id:
            c = db.query(Client).filter(Client.id == t.client_id, Client.company_id == company_id).first()
            if not c:
                continue

            to_email = getattr(c, "email", None)
            to_name = getattr(c, "nome", None)

            if getattr(c, "nome", None) is not None:
                ctx.setdefault("nome", c.nome)
            if getattr(c, "email", None) is not None:
                ctx.setdefault("email", c.email)
        else:
            to_email = t.email

        if not to_email:
            continue

        subject_rendered = _render_placeholders(tpl.assunto or "", ctx)
        body_rendered = _render_placeholders(tpl.corpo_html or "", ctx)

        log = EmailLog(
            company_id=company_id,
            client_id=t.client_id,
            template_id=camp.template_id,
            status="QUEUED",
            to_email=to_email,
            to_name=to_name,
            subject_rendered=subject_rendered,
            body_rendered=body_rendered,
            attempt_count=0,
            last_attempt_at=None,
            sent_at=None,
            created_at=now,
            campaign_id=camp.id,
            campaign_run_id=run.id,
        )

        db.add(log)
        db.flush()
        queued_log_ids.append(str(log.id))
        created_logs += 1

    db.commit()

    for log_id in queued_log_ids:
        send_email_job.delay(log_id)

    run = db.query(CampaignRun).filter(CampaignRun.id == run.id).first()
    if run:
        run.totals = {
            "total": int(created_logs),
            "sent": 0,
            "failed": 0,
            "cancelled": 0,
            "pending": int(created_logs),
            "by_status": {"QUEUED": int(created_logs)} if created_logs else {},
        }
        db.commit()
        db.refresh(run)

    return run


@router.get("/{campaign_id}/runs", response_model=List[CampaignRunOut], operation_id="campaigns_runs_list")
def list_campaign_runs(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    runs = (
        db.query(CampaignRun)
        .filter(CampaignRun.campaign_id == camp.id)
        .order_by(CampaignRun.started_at.desc())
        .all()
    )
    return runs


# =========================
# STATS (REAL TIME)
# =========================

@router.get("/{campaign_id}/stats", operation_id="campaigns_stats")
def get_campaign_stats(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    by_status = _calc_by_status_for_query(
        db,
        db.query(EmailLog.status, func.count(EmailLog.id))
        .filter(EmailLog.company_id == company_id)
        .filter(EmailLog.campaign_id == camp.id),
    )

    out = _normalize_stats(by_status)
    out.update({"campaign_id": str(camp.id), "campaign_status": camp.status})
    return out


# =========================
# PAUSE / RESUME / CANCEL
# =========================

@router.post("/{campaign_id}/pause", operation_id="campaigns_pause")
def pause_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    if camp.status != "running":
        raise HTTPException(status_code=400, detail="Campanha não está rodando")

    camp.status = "paused"

    run = _get_latest_run(db, str(camp.id))
    if run and run.status == "running":
        run.status = "paused"

    db.commit()

    updated = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .filter(EmailLog.campaign_id == camp.id)
        .filter(EmailLog.status.in_(["QUEUED", "SCHEDULED", "SENDING", "RETRYING", "DEFERRED"]))
        .update(
            {"status": "DEFERRED", "error_message": "Campaign paused"},
            synchronize_session=False,
        )
    )
    db.commit()

    return {"ok": True, "status": "paused", "updated_logs": int(updated)}


@router.post("/{campaign_id}/resume", operation_id="campaigns_resume")
def resume_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    if camp.status != "paused":
        raise HTTPException(status_code=400, detail=f"Campanha não está pausada (status={camp.status})")

    camp.status = "running"

    run = _get_latest_run(db, str(camp.id))
    if run and run.status == "paused":
        run.status = "running"

    db.commit()

    logs = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .filter(EmailLog.campaign_id == camp.id)
        .filter(EmailLog.status.in_(["DEFERRED", "PENDING", "RETRYING"]))
        .order_by(EmailLog.created_at.asc())
        .all()
    )

    ids_to_enqueue: List[str] = []
    for log in logs:
        log.status = "QUEUED"
        log.error_message = None
        db.flush()
        ids_to_enqueue.append(str(log.id))

    db.commit()

    for log_id in ids_to_enqueue:
        send_email_job.delay(log_id)

    return {"ok": True, "status": "running", "enqueued": int(len(ids_to_enqueue))}


@router.post("/{campaign_id}/cancel", operation_id="campaigns_cancel")
def cancel_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    camp.status = "cancelled"

    run = _get_latest_run(db, str(camp.id))
    if run and run.status in ("running", "paused"):
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)

    updated = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .filter(EmailLog.campaign_id == camp.id)
        .filter(EmailLog.status.in_(["QUEUED", "PENDING", "RETRYING", "SENDING", "DEFERRED"]))
        .update(
            {
                "status": "CANCELLED",
                "cancelled_at": datetime.now(timezone.utc),
                "cancelled_reason": "Campaign cancelled",
            },
            synchronize_session=False,
        )
    )

    db.commit()
    return {"ok": True, "status": "cancelled", "updated_logs": int(updated)}