"""The ref API client: sends the caller's token (never a service key) and turns refusals into clear errors."""

import json

import httpx2
import pytest

from opengxp.core.errors import GxPInputError
from opengxp.ref.client import RefAccessError, RefClient, config

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client(handler, token="user-jwt"):
    return RefClient(url="https://example.supabase.co", key="sb_publishable_x", token=token,
                     transport=httpx2.MockTransport(handler))


async def test_rpc_sends_caller_token_and_api_profile():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), auth=req.headers["authorization"], apikey=req.headers["apikey"],
                    profile=req.headers["content-profile"], body=json.loads(req.content))
        return httpx2.Response(200, json=[{"ok": 1}])

    assert await _client(handler).rpc("iid_rows", {"ingredient": "X"}) == [{"ok": 1}]
    assert seen == {"url": "https://example.supabase.co/rest/v1/rpc/iid_rows", "auth": "Bearer user-jwt",
                    "apikey": "sb_publishable_x", "profile": "api", "body": {"ingredient": "X"}}


@pytest.mark.parametrize("status,err,text", [
    (401, RefAccessError, "Not signed in"),
    (403, RefAccessError, "Access denied"),
    (404, RefAccessError, "not found"),
    (500, RefAccessError, "HTTP 500"),
    (400, GxPInputError, "kind must be"),
])
async def test_errors_mapped(status, err, text):
    def handler(req):
        return httpx2.Response(status, json={"message": "kind must be route, dosage_form or potency_unit"})

    with pytest.raises(err, match=text):
        await _client(handler).rpc("list_vocab", {"kind": "x"})


def test_config_refuses_secret_keys(monkeypatch):
    monkeypatch.setenv("OPENGXP_REF_URL", "https://example.supabase.co")
    monkeypatch.setenv("OPENGXP_REF_KEY", "sb_secret_abc")
    with pytest.raises(RefAccessError, match="bypasses row-level security"):
        config()
    # legacy JWT-style service_role key
    import base64
    payload = base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=")
    monkeypatch.setenv("OPENGXP_REF_KEY", f"h.{payload}.s")
    with pytest.raises(RefAccessError, match="bypasses"):
        config()


def test_config_requires_https(monkeypatch):
    monkeypatch.setenv("OPENGXP_REF_URL", "http://evil.example")
    monkeypatch.setenv("OPENGXP_REF_KEY", "sb_publishable_x")
    with pytest.raises(RefAccessError, match="not configured"):
        config()
