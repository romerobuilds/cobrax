from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel


class EmailSendRequest(BaseModel):
    client_id: UUID
    context: Dict[str, Any] = {}  # variáveis extras, ex: {"valor":"200", "vencimento":"..."}


class EmailSendResponse(BaseModel):
    log_id: UUID
    status: str
    subject: str
