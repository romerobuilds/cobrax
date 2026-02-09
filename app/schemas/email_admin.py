from typing import Optional, Literal
from pydantic import BaseModel, Field

ALLOWED_RATES = {5, 10, 15, 20, 25, 30}

class CompanyEmailSettingsUpdate(BaseModel):
    smtp_paused: Optional[bool] = None

    rate_per_min: Optional[int] = Field(default=None, description="5,10,15,20,25,30")
    daily_email_limit: Optional[int] = Field(default=None, ge=1, le=50000, description="Ex: 500, 1000...")

    # opcional: pra “remover limite” via API
    clear_daily_limit: Optional[bool] = False
