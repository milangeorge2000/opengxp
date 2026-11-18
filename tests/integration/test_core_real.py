"""Runs the real CDISC CORE engine on the public CDISC pilot study SDTM data.
Skipped unless OPENGXP_CORE (CORE executable) and OPENGXP_PILOT_SDTM (folder of pilot .xpt files) are set."""

import os

import pytest
from fastmcp import Client

from opengxp.server import mcp

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(
    not (os.environ.get("OPENGXP_CORE") and os.environ.get("OPENGXP_PILOT_SDTM")),
    reason="set OPENGXP_CORE and OPENGXP_PILOT_SDTM to run against real CORE")]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_real_core_on_pilot(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGXP_DATA_DIR", str(tmp_path))
    async with Client(mcp) as c:
        r = (await c.call_tool("sdtm_validate", {"datasets_path": os.environ["OPENGXP_PILOT_SDTM"],
                                                 "version": "3-2", "limit": 5})).structured_content
        assert r["core_version"] and r["totals"]["rules_run"] > 100
        assert r["totals"]["issues"] > 0 and r["groups"][0]["examples"]
        assert all("," not in i for g in r["groups"] for i in g["cdisc_rule_ids"])
        top = r["groups"][0]["key"]
        e = (await c.call_tool("sdtm_explain_rule", {"core_id": top})).structured_content
        assert e["source"] == "core_cache" and e["message"]
        sub = (await c.call_tool("sdtm_validate", {"datasets_path": os.environ["OPENGXP_PILOT_SDTM"], "version": "3-2",
                                                   "rule_ids": [top]})).structured_content
        d = (await c.call_tool("sdtm_diff_runs", {"before_run_id": r["run_id"], "after_run_id": sub["run_id"]})).structured_content
        assert d["new"] == 0 and d["fixed"] == 0 and d["rules_not_compared"]
