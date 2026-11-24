"""Calls the ref data API (Supabase PostgREST RPC functions in schema `api`) with the caller's own token,
so row-level security decides what each user may read. Never uses the service_role key."""

from __future__ import annotations

import os
from typing import Any

import httpx2

from opengxp.core.errors import GxPInputError

ENV_URL = "OPENGXP_REF_URL"   # https://<project>.supabase.co
ENV_KEY = "OPENGXP_REF_KEY"   # publishable / anon key (not a secret; RLS protects the data)


class RefAccessError(GxPInputError):
    """The caller is not signed in, not permitted, or the data service is not configured."""


def config() -> tuple[str, str]:
    url, key = os.environ.get(ENV_URL, "").rstrip("/"), os.environ.get(ENV_KEY, "")
    if not url.startswith("https://") and not url.startswith("http://localhost") and not url.startswith("http://127.0.0.1"):
        raise RefAccessError(f"Reference data is not configured: set {ENV_URL} (https://<project>.supabase.co) "
                             f"and {ENV_KEY} (the project's publishable key).")
    if not key:
        raise RefAccessError(f"Reference data is not configured: set {ENV_KEY} (the project's publishable key).")
    if key.startswith("sb_secret_") or '"service_role"' in _jwt_payload(key):
        raise RefAccessError(f"{ENV_KEY} is a secret/service_role key, which bypasses row-level security. "
                             "Use the publishable (anon) key.")
    return url, key


def _jwt_payload(token: str) -> str:
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        return ""
    try:
        return base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode("utf-8", "replace")
    except ValueError:
        return ""


def caller_token() -> str:
    """The bearer token of whoever is calling: the MCP request's OAuth token on HTTP, else the local login."""
    try:
        from fastmcp.server.dependencies import get_access_token

        tok = get_access_token()
    except Exception:  # noqa: BLE001 - no request context (stdio / tests)
        tok = None
    if tok is not None and getattr(tok, "token", None):
        return tok.token
    from opengxp.ref.session import current_access_token

    return current_access_token()


class RefClient:
    def __init__(self, url: str | None = None, key: str | None = None, token: str | None = None,
                 transport: httpx2.AsyncBaseTransport | None = None, timeout: float = 15.0):
        if url is None or key is None:
            url, key = config()
        self._url, self._key, self._token = url.rstrip("/"), key, token
        self._transport, self._timeout = transport, timeout

    async def rpc(self, fn: str, args: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        token = self._token or caller_token()
        headers = {"apikey": self._key, "Authorization": f"Bearer {token}", "Accept-Profile": "api",
                   "Content-Profile": "api", "Content-Type": "application/json"}
        async with httpx2.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            try:
                r = await http.post(f"{self._url}/rest/v1/rpc/{fn}", json=args or {}, headers=headers)
            except httpx2.HTTPError as e:
                raise RefAccessError(f"Reference data service unreachable ({type(e).__name__}). Try again later.") from None
        if r.status_code == 401:
            raise RefAccessError("Not signed in or the session expired. Run `opengxp-ref login` (local) or "
                                 "reconnect the MCP server (remote).")
        if r.status_code == 403:
            raise RefAccessError("Access denied by the data service. Ask your administrator for a role that "
                                 "grants this dataset (table ref.user_roles).")
        if r.status_code == 404:
            raise RefAccessError(f"Data function api.{fn} not found: the database migration is not applied or "
                                 "the 'api' schema is not exposed in the Supabase API settings.")
        if r.status_code == 400:
            msg = (r.json() or {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
            raise GxPInputError(f"Rejected by the data service: {msg or 'bad request'}")
        if r.status_code >= 300:
            raise RefAccessError(f"Reference data service error (HTTP {r.status_code}). Try again later.")
        data = r.json()
        return data if isinstance(data, list) else [data]
