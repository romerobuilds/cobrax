from __future__ import annotations

from typing import Any, Dict, List, Optional

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


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("results", "items", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    return []


def list_products_page(
    access_token: str,
    *,
    page: int = 1,
    limit: int = 100,
    timeout: int = 20,
) -> Dict[str, Any]:
    resp = requests.get(
        f"{CAKTO_API_BASE}/public_api/products/",
        headers=build_auth_headers(access_token),
        params={"page": page, "limit": limit},
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def list_orders_page(
    access_token: str,
    *,
    page: int = 1,
    limit: int = 100,
    timeout: int = 20,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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


def retrieve_order(
    access_token: str,
    order_id: str,
    timeout: int = 20,
) -> Dict[str, Any]:
    resp = requests.get(
        f"{CAKTO_API_BASE}/public_api/orders/{order_id}/",
        headers=build_auth_headers(access_token),
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def list_all_products(
    access_token: str,
    *,
    page_size: int = 100,
    max_pages: int = 30,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    pages = 0

    for page in range(1, max_pages + 1):
        payload = list_products_page(access_token, page=page, limit=page_size)
        rows = _extract_items(payload)
        pages += 1

        if not rows:
            break

        items.extend(rows)

        if len(rows) < page_size:
            break

    return {
        "items": items,
        "pages": pages,
    }


def list_all_orders(
    access_token: str,
    *,
    page_size: int = 100,
    max_pages: int = 30,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    pages = 0

    for page in range(1, max_pages + 1):
        payload = list_orders_page(access_token, page=page, limit=page_size, params=params)
        rows = _extract_items(payload)
        pages += 1

        if not rows:
            break

        items.extend(rows)

        if len(rows) < page_size:
            break

    return {
        "items": items,
        "pages": pages,
    }


# =========================
# WEBHOOKS
# =========================

def list_webhooks(
    access_token: str,
    *,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    limit: int = 100,
    timeout: int = 20,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"page": page, "limit": limit}
    if search:
        params["search"] = search
    if status:
        params["status"] = status

    resp = requests.get(
        f"{CAKTO_API_BASE}/public_api/webhook/",
        headers=build_auth_headers(access_token),
        params=params,
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def create_webhook(
    access_token: str,
    *,
    name: str,
    url: str,
    products: list[str],
    events: list[str],
    status: str = "active",
    timeout: int = 20,
) -> Dict[str, Any]:
    resp = requests.post(
        f"{CAKTO_API_BASE}/public_api/webhook/",
        headers=build_auth_headers(access_token),
        json={
            "name": name,
            "url": url,
            "products": products,
            "events": events,
            "status": status,
        },
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def update_webhook(
    access_token: str,
    webhook_id: int,
    *,
    name: str | None = None,
    url: str | None = None,
    products: list[str] | None = None,
    events: list[str] | None = None,
    status: str | None = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if url is not None:
        payload["url"] = url
    if products is not None:
        payload["products"] = products
    if events is not None:
        payload["events"] = events
    if status is not None:
        payload["status"] = status

    resp = requests.put(
        f"{CAKTO_API_BASE}/public_api/webhook/{webhook_id}/",
        headers=build_auth_headers(access_token),
        json=payload,
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def retrieve_webhook(
    access_token: str,
    webhook_id: int,
    timeout: int = 20,
) -> Dict[str, Any]:
    resp = requests.get(
        f"{CAKTO_API_BASE}/public_api/webhook/{webhook_id}/",
        headers=build_auth_headers(access_token),
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def test_webhook_event(
    access_token: str,
    webhook_id: int,
    timeout: int = 20,
) -> Dict[str, Any]:
    resp = requests.post(
        f"{CAKTO_API_BASE}/public_api/webhook/event_test/{webhook_id}/",
        headers=build_auth_headers(access_token),
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)
    return resp.json() or {}


def delete_webhook(
    access_token: str,
    webhook_id: int,
    timeout: int = 20,
) -> None:
    resp = requests.delete(
        f"{CAKTO_API_BASE}/public_api/webhook/{webhook_id}/",
        headers=build_auth_headers(access_token),
        timeout=timeout,
    )
    _raise_for_status_with_body(resp)