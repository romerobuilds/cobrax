# app/schemas/template_preview.py
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class TemplatePreviewRequest(BaseModel):
    # permite passar qualquer chave/valor, mas a validação final é no renderer
    context: Dict[str, Any] = Field(default_factory=dict)

class TemplatePreviewResponse(BaseModel):
    subject: str
    body: str
    used_vars: List[str]
