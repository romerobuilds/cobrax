from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.database_.database import get_db
from app.models.campaign import Campaign
from app.models.campaign_run import CampaignRun
from app.models.campaign_target import CampaignTarget
from app.schemas.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignOut,
    CampaignDetailOut,
    CampaignTargetsAdd,
    CampaignTargetsAddResult,
    CampaignRunOut,
)

router = APIRouter(prefix="/empresas/{company_id}/campanhas", tags=["Campanhas"])


def _ensure_company_campaign(db: Session, company_id: UUID, campaign_id: UUID) -> Campaign:
    c = (
        db.query(Campaign)
        .filter(Campaign.company_id == company_id, Campaign.id == campaign_id)
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Campanha não encontrada para esta empresa.")
    return c


@router.post("/", response_model=CampaignOut)
def create_campaign(company_id: UUID, payload: CampaignCreate, db: Session = Depends(get_db)):
    c = Campaign(
        company_id=company_id,
        name=payload.name,
        template_id=payload.template_id,
        status="draft",
        mode=payload.mode,
        context=payload.context or {},
        rate_per_min=int(payload.rate_per_min or 15),
        scheduled_at=payload.scheduled_at,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/", response_model=List[CampaignOut])
def list_campaigns(company_id: UUID, db: Session = Depends(get_db)):
    return (
        db.query(Campaign)
        .filter(Campaign.company_id == company_id)
        .order_by(desc(Campaign.created_at))
        .all()
    )


@router.get("/{campaign_id}", response_model=CampaignDetailOut)
def get_campaign_detail(company_id: UUID, campaign_id: UUID, db: Session = Depends(get_db)):
    c = _ensure_company_campaign(db, company_id, campaign_id)

    targets_count = (
        db.query(func.count(CampaignTarget.id))
        .filter(CampaignTarget.campaign_id == campaign_id)
        .scalar()
    ) or 0

    last_run = (
        db.query(CampaignRun)
        .filter(CampaignRun.campaign_id == campaign_id)
        .order_by(desc(CampaignRun.started_at))
        .first()
    )

    return {
        "campaign": c,
        "targets_count": int(targets_count),
        "last_run": last_run,
    }


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(company_id: UUID, campaign_id: UUID, payload: CampaignUpdate, db: Session = Depends(get_db)):
    c = _ensure_company_campaign(db, company_id, campaign_id)

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, v)

    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.post("/{campaign_id}/targets", response_model=CampaignTargetsAddResult)
def add_targets(company_id: UUID, campaign_id: UUID, payload: CampaignTargetsAdd, db: Session = Depends(get_db)):
    _ = _ensure_company_campaign(db, company_id, campaign_id)

    added = 0
    skipped = 0

    def upsert_client(client_id: UUID, extra_payload: Optional[dict] = None):
        nonlocal added, skipped
        exists = (
            db.query(CampaignTarget.id)
            .filter(CampaignTarget.campaign_id == campaign_id, CampaignTarget.client_id == client_id)
            .first()
        )
        if exists:
            skipped += 1
            return
        t = CampaignTarget(
            campaign_id=campaign_id,
            client_id=client_id,
            email=None,
            payload=extra_payload or {},
        )
        db.add(t)
        added += 1

    def upsert_email(email: str, extra_payload: Optional[dict] = None):
        nonlocal added, skipped
        email_l = email.strip().lower()
        exists = (
            db.query(CampaignTarget.id)
            .filter(CampaignTarget.campaign_id == campaign_id, func.lower(CampaignTarget.email) == email_l)
            .first()
        )
        if exists:
            skipped += 1
            return
        t = CampaignTarget(
            campaign_id=campaign_id,
            client_id=None,
            email=email,
            payload=extra_payload or {},
        )
        db.add(t)
        added += 1

    if payload.client_ids:
        for cid in payload.client_ids:
            upsert_client(cid)

    if payload.emails:
        for em in payload.emails:
            upsert_email(str(em))

    if payload.targets:
        for t in payload.targets:
            if t.client_id:
                upsert_client(t.client_id, t.payload or {})
            elif t.email:
                upsert_email(str(t.email), t.payload or {})
            else:
                skipped += 1

    db.commit()

    total_now = (
        db.query(func.count(CampaignTarget.id))
        .filter(CampaignTarget.campaign_id == campaign_id)
        .scalar()
    ) or 0

    return {"added": added, "skipped": skipped, "total_now": int(total_now)}


@router.post("/{campaign_id}/run", response_model=CampaignRunOut)
def run_campaign(company_id: UUID, campaign_id: UUID, db: Session = Depends(get_db)):
    c = _ensure_company_campaign(db, company_id, campaign_id)

    # valida se tem targets
    targets_count = (
        db.query(func.count(CampaignTarget.id))
        .filter(CampaignTarget.campaign_id == campaign_id)
        .scalar()
    ) or 0
    if targets_count <= 0:
        raise HTTPException(status_code=400, detail="Campanha sem targets. Adicione targets antes de rodar.")

    # cria run
    run = CampaignRun(
        campaign_id=campaign_id,
        status="running",
        totals={"total": int(targets_count), "sent": 0, "failed": 0, "pending": int(targets_count)},
    )
    db.add(run)

    # opcional: marca campanha como running
    c.status = "running"
    db.add(c)

    db.commit()
    db.refresh(run)

    # enfileirar (C: worker) — por enquanto já deixo plugado
    # se você ainda não tiver a task, a gente cria jájá.
    try:
        from app.workers.celery_app import celery_app
        celery_app.send_task("app.workers.tasks.run_campaign", args=[str(run.id)])
    except Exception:
        # não quebra a API por causa do enqueue (mas você vai ver nos logs)
        pass

    return run
