# app/services/asaas_client.py
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

import requests


def _asaas_base_url() -> str:
    return (os.getenv("ASAAS_BASE_URL") or "https://api.asaas.com/v3").rstrip("/")


def _asaas_headers() -> Dict[str, str]:
    api_key = (os.getenv("ASAAS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ASAAS_API_KEY não configurada no .env")

    user_agent = (os.getenv("ASAAS_USER_AGENT") or "COBRAX").strip()
    return {
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "access_token": api_key,
    }


def ensure_customer(name: str, email: str, cpf_cnpj: Optional[str] = None) -> str:
    """
    MVP: tenta buscar customer por email; se não achar, cria.
    """
    base = _asaas_base_url()
    headers = _asaas_headers()

    # busca
    r = requests.get(f"{base}/customers", headers=headers, params={"email": email}, timeout=20)
    r.raise_for_status()
    data = r.json() or {}
    items = data.get("data") or []
    if items and items[0].get("id"):
        return str(items[0]["id"])

    payload: Dict[str, Any] = {"name": name, "email": email}
    if cpf_cnpj:
        payload["cpfCnpj"] = cpf_cnpj

    r2 = requests.post(f"{base}/customers", headers=headers, json=payload, timeout=20)
    r2.raise_for_status()
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
    Retorna JSON do Asaas (tenta trazer invoiceUrl/bankSlipUrl/pdf).
    """
    base = _asaas_base_url()
    headers = _asaas_headers()

    payload: Dict[str, Any] = {
        "customer": customer_id,
        "billingType": "BOLETO",
        "value": float(value),  # ok para MVP
        "dueDate": due_date.isoformat(),
        "description": description,
    }
    if external_reference:
        payload["externalReference"] = external_reference

    r = requests.post(f"{base}/payments", headers=headers, json=payload, timeout=25)
    r.raise_for_status()
    return r.json() or {}