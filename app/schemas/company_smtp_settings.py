from typing import Optional
from pydantic import BaseModel, Field

ALLOWED_RATES = {5, 10, 15, 20, 25, 30}

class CompanySmtpSettingsUpdate(BaseModel):
    smtp_paused: Optional[bool] = None

    daily_email_limit: Optional[int] = Field(default=None, ge=0)

    rate_per_min: Optional[int] = None

    def validate_rate(self):
        if self.rate_per_min is not None and self.rate_per_min not in ALLOWED_RATES:
            raise ValueError("rate_per_min inválido. Use 5,10,15,20,25 ou 30.")
