from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests


DEFAULT_BASE = "https://api.asaas.com/v3"


def _asaas_base_url(base_url: Optional[str] = None) -> str:
    return (base_url or os.getenv("ASAAS_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")


def _asaas_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    final_api_key = (api_key or os.getenv("ASAAS_API_KEY") or "").strip()
    if not final_api_key:
        raise RuntimeError("ASAAS_API_KEY não configurada")

    user_agent = (os.getenv("ASAAS_USER_AGENT") or "COBRAX").strip()

    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": user_agent,
        "access_token": final_api_key,
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


def ping_asaas(
    api_key: str,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Faz uma chamada simples ao Asaas para validar credenciais/base_url.
    """
    base = _asaas_base_url(base_url)
    headers = _asaas_headers(api_key)

    r = requests.get(
        f"{base}/customers",
        headers=headers,
        params={"limit": 1, "offset": 0},
        timeout=20,
    )
    _raise_for_status_with_body(r)
    return r.json() or {}


def ensure_customer(
    name: str,
    email: str,
    cpf_cnpj: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """
    Busca customer por e-mail.
    Se existir, retorna o ID.
    Se não existir, cria.
    Se existir e vier cpf_cnpj válido mas o customer ainda não tiver cpfCnpj, atualiza.
    """
    if not str(name or "").strip():
        raise RuntimeError("name é obrigatório para criar customer no Asaas")

    if not str(email or "").strip():
        raise RuntimeError("email é obrigatório para criar customer no Asaas")

    base = _asaas_base_url(base_url)
    headers = _asaas_headers(api_key)

    cpf_cnpj_clean = _sanitize_cpf_cnpj(cpf_cnpj)
    email_clean = str(email).strip().lower()
    name_clean = str(name).strip()

    r = requests.get(
        f"{base}/customers",
        headers=headers,
        params={"email": email_clean},
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

    payload_create: Dict[str, Any] = {
        "name": name_clean,
        "email": email_clean,
    }
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
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cria cobrança boleto no Asaas.
    """
    if not str(customer_id or "").strip():
        raise RuntimeError("customer_id é obrigatório")

    if value is None:
        raise RuntimeError("value é obrigatório")

    if due_date is None:
        raise RuntimeError("due_date é obrigatório")

    if not str(description or "").strip():
        raise RuntimeError("description é obrigatória")

    base = _asaas_base_url(base_url)
    headers = _asaas_headers(api_key)

    value_2 = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    payload: Dict[str, Any] = {
        "customer": str(customer_id).strip(),
        "billingType": "BOLETO",
        "value": float(value_2),
        "dueDate": due_date.isoformat(),
        "description": str(description).strip(),
    }

    if external_reference:
        payload["externalReference"] = str(external_reference).strip()

    r = requests.post(
        f"{base}/payments",
        headers=headers,
        json=payload,
        timeout=25,
    )
    _raise_for_status_with_body(r)
    return r.json() or {}


def get_payment(
    payment_id: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Consulta pagamento pelo ID.
    """
    if not str(payment_id or "").strip():
        raise RuntimeError("payment_id é obrigatório")

    base = _asaas_base_url(base_url)
    headers = _asaas_headers(api_key)

    r = requests.get(
        f"{base}/payments/{str(payment_id).strip()}",
        headers=headers,
        timeout=20,
    )
    _raise_for_status_with_body(r)
    return r.json() or {}


def delete_payment(
    payment_id: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cancela/remove cobrança no Asaas.
    """
    if not str(payment_id or "").strip():
        raise RuntimeError("payment_id é obrigatório")

    base = _asaas_base_url(base_url)
    headers = _asaas_headers(api_key)

    r = requests.delete(
        f"{base}/payments/{str(payment_id).strip()}",
        headers=headers,
        timeout=20,
    )
    _raise_for_status_with_body(r)

    try:
        return r.json() or {}
    except Exception:
        return {"ok": True}


def _is_asaas_domain(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host.endswith("asaas.com")


def download_url_as_bytes(
    url: str,
    timeout: int = 25,
    *,
    api_key: Optional[str] = None,
) -> Tuple[bytes, str]:
    """
    Baixa um arquivo por URL.
    Se for domínio do Asaas, envia access_token automaticamente.
    """
    if not str(url or "").strip():
        raise RuntimeError("URL vazio para download")

    user_agent = (os.getenv("ASAAS_USER_AGENT") or "COBRAX").strip()
    headers: Dict[str, str] = {"User-Agent": user_agent}

    if _is_asaas_domain(url):
        final_api_key = (api_key or os.getenv("ASAAS_API_KEY") or "").strip()
        if final_api_key:
            headers["access_token"] = final_api_key

    r = requests.get(str(url).strip(), headers=headers, timeout=timeout)
    _raise_for_status_with_body(r)

    ct = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
    return (r.content or b""), ct