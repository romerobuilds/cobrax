# app/services/asaas_client.py
from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple

import requests


DEFAULT_BASE = "https://api.asaas.com/v3"


def _asaas_base_url() -> str:
    return (os.getenv("ASAAS_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")


def _asaas_headers() -> Dict[str, str]:
    api_key = (os.getenv("ASAAS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ASAAS_API_KEY não configurada no .env")

    user_agent = (os.getenv("ASAAS_USER_AGENT") or "COBRAX").strip()

    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": user_agent,
        "access_token": api_key,
    }


def _raise_for_status_with_body(resp: requests.Response) -> None:
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Asaas HTTP {resp.status_code}: {body}") from e


def build_external_reference(company_id: str, client_id: str) -> str:
    return f"company:{company_id}|client:{client_id}"


def _sanitize_cpf_cnpj(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = re.sub(r"\D+", "", str(value).strip())
    if not s:
        return None
    if len(s) not in (11, 14):
        return None
    return s


def ensure_customer(name: str, email: str, cpf_cnpj: Optional[str] = None) -> str:
    """
    - busca customer por email
    - se existir e cpf/cnpj veio e customer não tem, atualiza
    - se não existir: cria já com cpfCnpj (se válido)
    """
    base = _asaas_base_url()
    headers = _asaas_headers()

    cpf_cnpj_clean = _sanitize_cpf_cnpj(cpf_cnpj)

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
        customer = items[0]
        cid = str(customer["id"])

        current_doc = customer.get("cpfCnpj")
        if cpf_cnpj_clean and not current_doc:
            payload: Dict[str, Any] = {"cpfCnpj": cpf_cnpj_clean}
            r_upd = requests.put(
                f"{base}/customers/{cid}",
                headers=headers,
                json=payload,
                timeout=20,
            )
            _raise_for_status_with_body(r_upd)

        return cid

    payload_create: Dict[str, Any] = {"name": name, "email": email}
    if cpf_cnpj_clean:
        payload_create["cpfCnpj"] = cpf_cnpj_clean

    r2 = requests.post(
        f"{base}/customers",
        headers=headers,
        json=payload_create,
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
    base = _asaas_base_url()
    headers = _asaas_headers()

    value_2 = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    payload: Dict[str, Any] = {
        "customer": customer_id,
        "billingType": "BOLETO",
        "value": float(value_2),
        "dueDate": due_date.isoformat(),
        "description": description,
    }
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


def download_url_as_bytes(url: str, timeout: int = 25) -> Tuple[bytes, str]:
    """
    Baixa um URL e retorna (bytes, content_type).
    Serve pra anexar PDF do boleto.
    """
    if not url:
        raise RuntimeError("URL vazio para download")

    headers = {"User-Agent": (os.getenv("ASAAS_USER_AGENT") or "COBRAX").strip()}

    r = requests.get(url, headers=headers, timeout=timeout)
    _raise_for_status_with_body(r)

    ct = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
    return (r.content or b""), ct