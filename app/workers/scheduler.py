# app/workers/scheduler.py
from datetime import datetime, timezone
from app.workers.celery_app import celery_app
from app.database_.database import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_run import CampaignRun
from app.models.campaign_target import CampaignTarget
from app.models.email_template import EmailTemplate
from app.models.email_log import EmailLog
from app.models.client import Client
from app.workers.tasks import send_email_job


@celery_app.task
def check_scheduled_campaigns():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        campaigns = (
            db.query(Campaign)
            .filter(
                Campaign.status.in_(["draft", "ready"]),
                Campaign.scheduled_at != None,
                Campaign.scheduled_at <= now
            )
            .all()
        )

        for camp in campaigns:

            # evita rodar duas vezes
            existing_running = (
                db.query(CampaignRun)
                .filter(
                    CampaignRun.campaign_id == camp.id,
                    CampaignRun.status == "running"
                )
                .first()
            )

            if existing_running:
                continue

            run = CampaignRun(
                campaign_id=camp.id,
                status="running",
                totals={}
            )

            db.add(run)
            camp.status = "running"
            db.commit()
            db.refresh(run)

            tpl = db.query(EmailTemplate).filter(EmailTemplate.id == camp.template_id).first()
            if not tpl:
                continue

            targets = db.query(CampaignTarget).filter(CampaignTarget.campaign_id == camp.id).all()

            for t in targets:

                to_email = None
                to_name = None
                ctx = dict(camp.context or {})

                if t.payload:
                    ctx.update(t.payload)

                if t.client_id:
                    client = db.query(Client).filter(Client.id == t.client_id).first()
                    if not client:
                        continue
                    to_email = client.email
                    to_name = getattr(client, "nome", None)
                else:
                    to_email = t.email

                if not to_email:
                    continue

                subject = tpl.assunto or ""
                body = tpl.html or tpl.body or ""

                for k, v in ctx.items():
                    subject = subject.replace("{{" + k + "}}", str(v))
                    body = body.replace("{{" + k + "}}", str(v))

                log = EmailLog(
                    company_id=camp.company_id,
                    client_id=t.client_id,
                    template_id=camp.template_id,
                    status="QUEUED",
                    to_email=to_email,
                    to_name=to_name,
                    subject_rendered=subject,
                    body_rendered=body,
                    attempt_count=0,
                    campaign_id=camp.id,
                    campaign_run_id=run.id,
                )

                db.add(log)
                db.flush()

                send_email_job.delay(str(log.id))

            db.commit()

    finally:
        db.close()
