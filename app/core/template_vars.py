from typing import Dict, Any

ALLOWED_TEMPLATE_VARS = {
    "nome",
    "email",
    "telefone",
    "empresa_nome",
    "empresa_email",
    "valor",
    "vencimento",
    "numero_fatura",
    "descricao",
    "observacao",
    "link_pagamento",
    "link_boleto",
    "linha_digitavel",
    "contrato",

    # Cakto / pedidos
    "pedido_id",
    "produto_id",
    "pedido_status",
    "forma_pagamento",
    "valor_pedido",
    "data_pedido",
    "utm_source",
    "utm_medium",
    "utm_campaign",
}

def build_default_context(*, company, client, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ctx = {
        "nome": getattr(client, "nome", None),
        "email": getattr(client, "email", None),
        "telefone": getattr(client, "telefone", None),

        "empresa_nome": getattr(company, "nome", None),
        "empresa_email": getattr(company, "email", None),

        "valor": None,
        "vencimento": None,
        "numero_fatura": None,
        "descricao": None,
        "observacao": None,
        "link_pagamento": None,
        "link_boleto": None,
        "linha_digitavel": None,
        "contrato": None,

        # Cakto / pedidos
        "pedido_id": None,
        "produto_id": None,
        "pedido_status": None,
        "forma_pagamento": None,
        "valor_pedido": None,
        "data_pedido": None,
        "utm_source": None,
        "utm_medium": None,
        "utm_campaign": None,
    }

    if extra:
        ctx.update(extra)

    return ctx