from typing import Dict, Any

ALLOWED_TEMPLATE_VARS = {
    "nome_cliente",
    "email_cliente",
    "telefone_cliente",
    "empresa_nome",
    "empresa_email",
    "valor",
    "vencimento",
    "linha_digitavel",
    "link_boleto",
}

def build_default_context(*, company, client, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ctx = {
        "nome_cliente": getattr(client, "nome", None),
        "email_cliente": getattr(client, "email", None),
        "telefone_cliente": getattr(client, "telefone", None),
        "empresa_nome": getattr(company, "nome", None),
        "empresa_email": getattr(company, "email", None),
        "valor": None,
        "vencimento": None,
        "linha_digitavel": None,
        "link_boleto": None,
    }
    if extra:
        ctx.update(extra)
    return ctx
