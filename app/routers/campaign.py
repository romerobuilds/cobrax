# app/routers/campaign.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

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


router = APIRouter(prefix="/empresas/{company_id}/campanhas", tags=["Campanhas"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


@router.get("/", response_model=List[CampaignOut])
def list_campaigns(company_id: str, db: Session = Depends(get_db)):
    items = (
        db.query(Campaign)
        .filter(Campaign.company_id == company_id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return items


@router.post("/", response_model=CampaignOut)
def create_campaign(company_id: str, body: CampaignCreate, db: Session = Depends(get_db)):
    # valida template
    tpl = db.query(EmailTemplate).filter(EmailTemplate.id == body.template_id).first()
    if not tpl:
        raise HTTPException(status_code=400, detail="template_id inválido")

    camp = Campaign(
        company_id=company_id,
        name=body.name,
        template_id=body.template_id,
        status="draft",
        mode=body.mode or "selected",
        context=body.context or {},
        rate_per_min=int(body.rate_per_min or 15),
        scheduled_at=body.scheduled_at,
    )
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return camp


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    return _ensure_campaign_company(db, company_id, campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(company_id: str, campaign_id: str, body: CampaignUpdate, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    data = body.model_dump(exclude_unset=True)
    if "template_id" in data:
        tpl = db.query(EmailTemplate).filter(EmailTemplate.id == data["template_id"]).first()
        if not tpl:
            raise HTTPException(status_code=400, detail="template_id inválido")

    for k, v in data.items():
        setattr(camp, k, v)

    db.commit()
    db.refresh(camp)
    return camp


@router.delete("/{campaign_id}")
def delete_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)
    db.delete(camp)
    db.commit()
    return {"ok": True}


@router.post("/{campaign_id}/targets/selected")
def add_targets_selected(
    company_id: str,
    campaign_id: str,
    body: CampaignTargetAddSelected,
    db: Session = Depends(get_db),
):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    added = 0
    for cid in body.client_ids or []:
        # valida client
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
    return {"ok": True, "added": added}


@router.post("/{campaign_id}/targets/all")
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
    return {"ok": True, "added": added}


@router.post("/{campaign_id}/targets/emails")
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
    return {"ok": True, "added": added}


@router.post("/{campaign_id}/run", response_model=CampaignRunOut)
def start_campaign_run(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    if camp.status not in ("draft", "ready"):
        # deixa passar running/done? aqui a gente bloqueia pra evitar duplicar sem querer
        raise HTTPException(
            status_code=400,
            detail=f"Campanha em status inválido para iniciar: {camp.status}",
        )

    tpl = db.query(EmailTemplate).filter(EmailTemplate.id == camp.template_id).first()
    if not tpl:
        raise HTTPException(status_code=400, detail="Template não encontrado")

    targets = db.query(CampaignTarget).filter(CampaignTarget.campaign_id == camp.id).all()
    if not targets:
        raise HTTPException(status_code=400, detail="Campanha sem targets")

    run = CampaignRun(campaign_id=camp.id, status="running", totals={})
    db.add(run)
    camp.status = "running"
    db.commit()
    db.refresh(run)

    created_logs = 0

    for t in targets:
        to_email = None
        to_name = None
        ctx: Dict[str, Any] = dict(camp.context or {})

        # payload por target tem prioridade
        if t.payload:
            ctx.update(t.payload)

        if t.client_id:
            c = db.query(Client).filter(Client.id == t.client_id, Client.company_id == company_id).first()
            if not c:
                continue
            to_email = getattr(c, "email", None)
            to_name = getattr(c, "nome", None) or getattr(c, "name", None)

            # também joga alguns campos do cliente no ctx (útil no template)
            if getattr(c, "nome", None) is not None:
                ctx.setdefault("nome", c.nome)
            if getattr(c, "email", None) is not None:
                ctx.setdefault("email", c.email)
        else:
            to_email = t.email
            to_name = None

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

        # ⚠️ NÃO passe id=None (deixa o default do model gerar)
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
            created_at=datetime.now(timezone.utc),
            campaign_id=camp.id,
            campaign_run_id=run.id,
        )
        db.add(log)
        db.flush()  # garante log.id antes de mandar pro celery
        created_logs += 1

        # enfileira
        send_email_job.delay(str(log.id))

    db.commit()

    # salva totals iniciais
    run.totals = {
        "total": created_logs,
        "sent": 0,
        "failed": 0,
        "pending": created_logs,
        "by_status": {"QUEUED": created_logs},
    }
    db.commit()
    db.refresh(run)

    return run


@router.get("/{campaign_id}/runs", response_model=List[CampaignRunOut])
def list_campaign_runs(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    runs = (
        db.query(CampaignRun)
        .filter(CampaignRun.campaign_id == camp.id)
        .order_by(CampaignRun.started_at.desc())
        .all()
    )
    return runs


@router.get("/runs/{run_id}", response_model=CampaignRunOut)
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


# =========================
# PAUSE / RESUME / CANCEL
# =========================

@router.post("/{campaign_id}/pause", operation_id="pause_campaign")
def pause_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    if camp.status != "running":
        raise HTTPException(status_code=400, detail="Campanha não está rodando")

    camp.status = "paused"
    db.commit()

    # 🔥 efeito imediato na UI: joga statuses "em andamento" pra PENDING
    updated = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .filter(EmailLog.campaign_id == camp.id)
        .filter(EmailLog.status.in_(["QUEUED", "SCHEDULED", "SENDING", "RETRYING", "DEFERRED"]))
        .update(
            {
                "status": "PENDING",
                "error_message": "Campaign paused",
            },
            synchronize_session=False,
        )
    )
    db.commit()

    return {"ok": True, "status": "paused", "updated_logs": int(updated)}


@router.post("/{campaign_id}/resume", operation_id="resume_campaign")
def resume_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    if camp.status != "paused":
        raise HTTPException(status_code=400, detail=f"Campanha não está pausada (status={camp.status})")

    camp.status = "running"
    db.commit()

    # re-enfileira PENDING/DEFERRED/RETRYING
    logs = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .filter(EmailLog.campaign_id == camp.id)
        .filter(EmailLog.status.in_(["PENDING", "DEFERRED", "RETRYING"]))
        .order_by(EmailLog.created_at.asc())
        .all()
    )

    enqueued = 0
    for log in logs:
        log.status = "QUEUED"
        log.error_message = None
        db.flush()
        send_email_job.delay(str(log.id))
        enqueued += 1

    db.commit()

    return {"ok": True, "status": "running", "enqueued": int(enqueued)}


@router.post("/{campaign_id}/cancel", operation_id="cancel_campaign")
def cancel_campaign(company_id: str, campaign_id: str, db: Session = Depends(get_db)):
    camp = _ensure_campaign_company(db, company_id, campaign_id)

    # cancela campanha
    camp.status = "cancelled"

    # cancela logs ainda não enviados
    db.query(EmailLog).filter(
        EmailLog.company_id == company_id,
        EmailLog.campaign_id == camp.id,
        EmailLog.status.in_(["QUEUED", "PENDING", "RETRYING", "SENDING", "DEFERRED"]),
    ).update(
        {
            "status": "CANCELLED",
            "cancelled_at": datetime.now(timezone.utc),
            "cancelled_reason": "Campaign cancelled",
        },
        synchronize_session=False,
    )

    db.commit()

    return {"ok": True, "status": "cancelled"}
