from __future__ import annotations

from typing import Any, Dict, Optional

import requests


CAKTO_API_BASE = "https://api.cakto.com.br"
CAKTO_TOKEN_URL = f"{CAKTO_API_BASE}/public_api/token/"


def _raise_for_status_with_body(resp: requests.Response) -> None:
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(f"Cakto HTTP {resp.status_code}: {body}") from e


def get_access_token(
    client_id: str,
    client_secret: str,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Troca client_id + client_secret por access_token na Cakto.
    Documentação oficial:
    POST https://api.cakto.com.br/public_api/token/
    """
    client_id = str(client_id or "").strip()
    client_secret = str(client_secret or "").strip()

    if not client_id:
        raise RuntimeError("cakto_client_id não configurado")

    if not client_secret:
        raise RuntimeError("cakto_client_secret não configurado")

    resp = requests.post(
        CAKTO_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)

    data = resp.json() or {}
    access_token = str(data.get("access_token") or "").strip()
    token_type = str(data.get("token_type") or "").strip()

    if not access_token:
        raise RuntimeError(f"Resposta da Cakto sem access_token: {data}")

    if token_type and token_type.lower() != "bearer":
        raise RuntimeError(f"token_type inesperado retornado pela Cakto: {token_type}")

    return data


def test_credentials(
    client_id: str,
    client_secret: str,
) -> Dict[str, Any]:
    """
    Valida credenciais obtendo token OAuth2.
    Não depende de escopos específicos além dos da própria chave.
    """
    token_data = get_access_token(client_id=client_id, client_secret=client_secret)

    return {
        "ok": True,
        "token_type": token_data.get("token_type"),
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "has_access_token": bool(token_data.get("access_token")),
    }


def build_auth_headers(access_token: str) -> Dict[str, str]:
    token = str(access_token or "").strip()
    if not token:
        raise RuntimeError("access_token vazio")

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def list_products(
    access_token: str,
    *,
    page: int = 1,
    limit: int = 20,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Helper já pronto para a próxima fase.
    """
    resp = requests.get(
        f"{CAKTO_API_BASE}/public_api/products/",
        headers=build_auth_headers(access_token),
        params={"page": page, "limit": limit},
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def list_orders(
    access_token: str,
    *,
    page: int = 1,
    limit: int = 20,
    timeout: int = 20,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Helper já pronto para a próxima fase.
    """
    final_params: Dict[str, Any] = {"page": page, "limit": limit}
    if params:
        final_params.update(params)

    resp = requests.get(
        f"{CAKTO_API_BASE}/public_api/orders/",
        headers=build_auth_headers(access_token),
        params=final_params,
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}