# app/routes/plan_admin.py
from typing import List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.models.plan import Plan
from app.models.user import User

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.get("/", status_code=status.HTTP_200_OK)
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    plans = db.query(Plan).order_by(Plan.created_at.asc()).all()
    return [
        {
            "id": str(p.id),
            "code": p.code,
            "name": p.name,
            "rate_per_min": p.rate_per_min,
            "daily_email_limit": p.daily_email_limit,
        }
        for p in plans
    ]


@router.post("/empresas/{company_id}/set", status_code=status.HTTP_200_OK)
def set_company_plan(
    company_id: UUID,
    plan_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user.id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plano não encontrado")

    company.plan_id = plan.id
    db.commit()

    return {"company_id": str(company.id), "plan_id": str(plan.id), "plan_code": plan.code}
