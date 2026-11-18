"""CDISC CORE adapter: CLI invocation, report grouping/paging, stored runs, diffs, rule explanations.
The real engine is replaced by tests/cdisc/fake_core.py, which writes CORE's v1.0.0 JSON report format."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from opengxp.cdisc import report as rep
from opengxp.cdisc.server import sdtm
from tests.cdisc.conftest import make_report

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(name, args):
    async with Client(sdtm) as c:
        return (await c.call_tool(name, args)).structured_content


async def test_validate_groups_by_rule_and_builds_cli(core_env):
    r = await call("sdtm_validate", {"datasets_path": str(core_env["data"]), "version": "3.4",
                                     "define_xml_path": str(core_env["data"] / "define.xml"), "ct_packages": ["sdtmct-2024-09-27"]})
    args = json.loads(core_env["args"].read_text())
    assert args[:5] == ["validate", "-s", "sdtmig", "-v", "3-4"] and "-of" in args and "JSON" in args
    assert args.count("-dp") == 2                                    # define.xml not passed as a dataset
    assert "--define-xml-path" in args and "sdtmct-2024-09-27" in args
    assert r["totals"] == {"issues": 6, "rules_with_issues": 2, "rules_run": 4, "rules_skipped": 1, "execution_errors": 1}
    g = r["groups"][0]
    assert (g["key"], g["count"], g["cdisc_rule_ids"], g["fda_rule_ids"]) == ("CORE-000001", 4, ["CG0001"], ["SD1022"])
    assert len(g["examples"]) == 3 and g["examples"][0]["variables"] == ["AESTDTC", "AEENDTC"]
    assert len(r["execution_errors"]) == 1
    assert r["groups"][1]["cdisc_rule_ids"] == ["CG0123", "TIG0347"]       # CORE joins IDs with commas
    assert {f["code"] for f in r["findings"]} == {"CONFORMANCE_ISSUES", "RULE_EXECUTION_ERRORS", "RULES_SKIPPED"}


async def test_paging_and_filters(core_env):
    r = await call("sdtm_validate", {"datasets_path": str(core_env["data"]), "limit": 1})
    assert r["next_cursor"] == 1
    p2 = await call("sdtm_get_run", {"run_id": r["run_id"], "limit": 1, "cursor": 1})
    assert p2["groups"][0]["key"] == "CORE-000123" and p2["next_cursor"] is None
    dm = await call("sdtm_get_run", {"run_id": r["run_id"], "domains": ["dm"]})
    assert [g["key"] for g in dm["groups"]] == ["CORE-000123"]
    by_ds = await call("sdtm_get_run", {"run_id": r["run_id"], "group_by": "dataset"})
    assert {g["key"] for g in by_ds["groups"]} == {"ae.xpt", "dm.xpt"}


async def test_rule_subset_passed_to_core(core_env):
    await call("sdtm_validate", {"datasets_path": str(core_env["data"]), "rule_ids": ["CORE-000123"]})
    args = json.loads(core_env["args"].read_text())
    assert args[args.index("-r") + 1] == "CORE-000123"


async def test_fix_loop_diff(core_env):
    before = await call("sdtm_validate", {"datasets_path": str(core_env["data"])})
    core_env["report"].write_text(json.dumps(make_report(n_ae=1, n_dm=0, with_error=False)))
    after = await call("sdtm_validate", {"datasets_path": str(core_env["data"])})
    d = await call("sdtm_diff_runs", {"before_run_id": before["run_id"], "after_run_id": after["run_id"]})
    assert (d["fixed"], d["new"], d["unchanged"], d["progress"], d["clean"]) == (6, 0, 1, True, False)
    by = {x["core_id"]: (x["before"], x["after"]) for x in d["by_rule"]}
    assert by["CORE-000001"] == (4, 1) and by["CORE-000123"] == (2, 0)


async def test_explain_rule_from_cache(core_env):
    e = await call("sdtm_explain_rule", {"core_id": "CORE-000001"})
    assert e["source"] == "core_cache" and e["message"] == "AESTDTC is after AEENDTC"
    assert e["references"][0]["rule_id"] == "CG0001" and e["references"][0]["section"] == "4.4.7"
    assert "date_greater_than" in e["check_logic"]


async def test_status_and_errors(core_env, monkeypatch, tmp_path):
    s = await call("sdtm_core_status", {})
    assert s["configured"] and s["core_version"] == "1.0.0"
    with pytest.raises(ToolError, match="No dataset files"):
        await call("sdtm_validate", {"datasets_path": str(tmp_path / "store")})
    with pytest.raises(ToolError, match="version"):
        await call("sdtm_validate", {"datasets_path": str(core_env["data"]), "version": "three"})
    with pytest.raises(ToolError, match="Unknown run_id"):
        await call("sdtm_get_run", {"run_id": "nope"})
    monkeypatch.delenv("OPENGXP_CORE")
    s = await call("sdtm_core_status", {})
    assert not s["configured"] and "releases" in s["setup"]
    with pytest.raises(ToolError, match="not configured"):
        await call("sdtm_validate", {"datasets_path": str(core_env["data"])})


async def test_rule_subset_rerun_is_not_counted_as_fixed(core_env):
    full = await call("sdtm_validate", {"datasets_path": str(core_env["data"])})
    rpt = make_report()
    rpt["Rules_Report"] = [r for r in rpt["Rules_Report"] if r["core_id"] == "CORE-000123"]
    core_env["report"].write_text(json.dumps(rpt))
    sub = await call("sdtm_validate", {"datasets_path": str(core_env["data"]), "rule_ids": ["CORE-000123"]})
    d = await call("sdtm_diff_runs", {"before_run_id": full["run_id"], "after_run_id": sub["run_id"]})
    assert (d["fixed"], d["unchanged"], d["progress"]) == (0, 2, False)
    assert "CORE-000001" in d["rules_not_compared"]


def test_run_id_path_traversal_rejected(core_env):
    with pytest.raises(Exception, match="Invalid run_id"):
        rep.load_run("../../etc/passwd")
