# app/services/asaas_client.py
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

import requests


DEFAULT_BASE = "https://api.asaas.com/v3"


def _asaas_base_url() -> str:
    # Ex: https://sandbox.asaas.com/api/v3
    return (os.getenv("ASAAS_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")


def _asaas_headers() -> Dict[str, str]:
    api_key = (os.getenv("ASAAS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ASAAS_API_KEY não configurada no .env")

    user_agent = (os.getenv("ASAAS_USER_AGENT") or "COBRAX").strip()

    # Asaas usa header "access_token"
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": user_agent,
        "access_token": api_key,
    }


def _raise_for_status_with_body(resp: requests.Response) -> None:
    """
    Melhora a mensagem de erro quando o Asaas responde 4xx/5xx.
    """
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = None
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Asaas HTTP {resp.status_code}: {body}") from e


def build_external_reference(company_id: str, client_id: str) -> str:
    """
    Formato que o seu webhook já sabe interpretar:
      company:<uuid>|client:<uuid>
    """
    return f"company:{company_id}|client:{client_id}"


def ensure_customer(name: str, email: str, cpf_cnpj: Optional[str] = None) -> str:
    """
    MVP: tenta buscar customer por email; se não achar, cria.
    """
    base = _asaas_base_url()
    headers = _asaas_headers()

    r = requests.get(
        f"{base}/customers",
        headers=headers,
        params={"email": email},
        timeout=20,
    )
    _raise_for_status_with_body(r)

    data = r.json() or {}
    items = data.get("data") or []
    if items and items[0].get("id"):
        return str(items[0]["id"])

    payload: Dict[str, Any] = {"name": name, "email": email}
    if cpf_cnpj:
        payload["cpfCnpj"] = cpf_cnpj

    r2 = requests.post(
        f"{base}/customers",
        headers=headers,
        json=payload,
        timeout=20,
    )
    _raise_for_status_with_body(r2)

    created = r2.json() or {}
    cid = created.get("id")
    if not cid:
        raise RuntimeError(f"Falha ao criar customer no Asaas: {created}")
    return str(cid)


def create_boleto_payment(
    customer_id: str,
    value: Decimal,
    due_date: date,
    description: str,
    external_reference: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cria cobrança via boleto.
    Retorna JSON do Asaas (invoiceUrl/bankSlipUrl/etc).
    """
    base = _asaas_base_url()
    headers = _asaas_headers()

    # evita erro de arredondamento no float
    value_2 = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    payload: Dict[str, Any] = {
        "customer": customer_id,
        "billingType": "BOLETO",
        "value": float(value_2),
        "dueDate": due_date.isoformat(),
        "description": description,
    }

    # ESSENCIAL pro seu webhook mapear e salvar no banco
    if external_reference:
        payload["externalReference"] = external_reference

    r = requests.post(
        f"{base}/payments",
        headers=headers,
        json=payload,
        timeout=25,
    )
    _raise_for_status_with_body(r)
    return r.json() or {}