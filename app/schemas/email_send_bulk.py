from typing import Dict, Any, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field

ALLOWED_RATES = {5, 10, 15, 20, 25, 30}

class EmailSendBulkRequest(BaseModel):
    client_ids: Optional[List[UUID]] = None
    context: Dict[str, Any] = Field(default_factory=dict)

    # ✅ novo
    rate_per_min: int = Field(default=15, description="Envios por minuto: 5,10,15,20,25,30")
