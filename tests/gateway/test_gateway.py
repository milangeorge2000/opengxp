import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from opengxp.core import GxPInputError
from opengxp.gateway.approvals import ApprovalStore
from opengxp.gateway.audit import AuditLog
from opengxp.gateway.cli import build_gateway, main
from opengxp.gateway.policy import Policy, RoleRule, decide
from opengxp.gateway.registry import Registry
from opengxp.gateway.reports import audit_report, validation_summary
from tests.gateway.target_server import srv

pytestmark = pytest.mark.anyio
PW = "correct horse battery"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGXP_USER", "analyst1")
    monkeypatch.setenv("OPENGXP_ROLE", "qc")
    ApprovalStore(tmp_path).add_signer("qa_lead", "Dr. A. Reviewer", PW)
    ApprovalStore(tmp_path).add_signer("analyst1", "B. Analyst", PW)

    def make(policy=None, registry=None, target=srv):
        return build_gateway(target, policy=policy or Policy(), registry=registry or Registry(), root=tmp_path)
    make.root = tmp_path
    return make


def log_of(root):
    return list(AuditLog(root / "audit.jsonl"))


# ---- audit chain ------------------------------------------------------------------------------------
def test_chain_detects_edit_delete_reorder(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    for i in range(5):
        log.append({"event": "x", "i": i})
    assert log.verify().ok and log.verify().records == 5
    lines = (tmp_path / "a.jsonl").read_text().splitlines()

    edited = lines.copy(); r = json.loads(edited[2]); r["i"] = 99; edited[2] = json.dumps(r)
    (tmp_path / "e.jsonl").write_text("\n".join(edited) + "\n")
    v = AuditLog(tmp_path / "e.jsonl").verify()
    assert not v.ok and v.first_bad_seq == 3 and "edited" in v.reason

    (tmp_path / "d.jsonl").write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert "gap" in AuditLog(tmp_path / "d.jsonl").verify().reason

    (tmp_path / "r.jsonl").write_text("\n".join([lines[1], lines[0]] + lines[2:]) + "\n")
    assert not AuditLog(tmp_path / "r.jsonl").verify().ok


# ---- policy ------------------------------------------------------------------------------------------
def test_policy_decisions():
    p = Policy(roles={"qc": {"allow": ["cmc_*", "get_*"], "deny": ["cmc_elemental*"]}, "default": {"allow": []}},
               validated_only_roles=["qc"])
    assert not decide(p, "qc", False, "cmc_x", True, True).allowed                 # no identity
    assert decide(p, "qc", True, "cmc_impurity_thresholds", True, True).allowed
    assert not decide(p, "qc", True, "cmc_elemental_impurity_risk", True, True).allowed
    assert not decide(p, "qc", True, "stab_x", True, True).allowed                 # not in allow
    assert not decide(p, "qc", True, "cmc_x", True, False).allowed                 # not validated
    assert decide(Policy(), "default", True, "w", False, True).needs_approval       # write -> approval


# ---- approvals / e-signatures --------------------------------------------------------------------------
def test_signature_rules(tmp_path):
    st = ApprovalStore(tmp_path)
    st.add_signer("qa", "QA Person", PW)
    st.add_signer("me", "Me", PW)
    a = st.request("me", "update", "h1", "{}", 60)
    assert st.request("me", "update", "h1", "{}", 60).id == a.id                  # reuse open request
    with pytest.raises(GxPInputError, match="wrong password"):
        st.sign(a.id, "qa", "nope-nope-nope", "approved", "ok")
    with pytest.raises(GxPInputError, match="own request"):
        st.sign(a.id, "me", PW, "approved", "ok")
    with pytest.raises(GxPInputError, match="reason"):
        st.sign(a.id, "qa", PW, "approved", " ")
    s = st.sign(a.id, "qa", PW, "approved", "Checked batch record")
    assert s.signature.printed_name == "QA Person" and s.status == "approved"
    assert st.consume("me", "update", "other-args", ) is None                      # bound to exact inputs
    assert st.consume("me", "update", "h1").id == a.id
    assert st.consume("me", "update", "h1") is None                                # single use
    with pytest.raises(GxPInputError, match="12 characters"):
        st.add_signer("x", "X", "short")
    assert "pw_hash" in (tmp_path / "signers.json").read_text() and PW not in (tmp_path / "signers.json").read_text()


def test_expired_approval(tmp_path):
    st = ApprovalStore(tmp_path)
    a = st.request("me", "t", "h", "{}", ttl_minutes=-1)
    assert st.get(a.id).status == "expired"


# ---- middleware end to end -------------------------------------------------------------------------------
async def test_read_only_call_is_audited(gw):
    g = gw()
    async with Client(g) as c:
        r = await c.call_tool("get_batch", {"batch_id": "B1"})
    assert r.structured_content["status"] == "released"
    rec = log_of(gw.root)[-1]
    assert rec["event"] == "executed" and rec["actor"] == {"user_id": "analyst1", "role": "qc", "roles": ["qc"], "auth_method": "local-config"}
    assert rec["inputs"] == {"batch_id": "B1"} and len(rec["outputs_sha256"]) == 64
    assert rec["output_summary"] == "Batch B1: released" and rec["validation_status"] == "not_validated"
    assert rec["agent"]["name"] == "fastmcp" and rec["agent"]["version"]


async def test_write_needs_signed_single_use_approval(gw):
    g = gw()
    args = {"batch_id": "B7", "status": "rejected"}
    async with Client(g) as c:
        with pytest.raises(ToolError, match=r"Approval required \(APR-"):
            await c.call_tool("update_batch_status", args)
        apr = ApprovalStore(gw.root).list("pending")[0]
        ApprovalStore(gw.root).sign(apr.id, "qa_lead", PW, "approved", "Deviation DV-12 closed")
        with pytest.raises(ToolError, match="Approval required"):               # different inputs: not covered
            await c.call_tool("update_batch_status", {"batch_id": "B7", "status": "released"})
        r = await c.call_tool("update_batch_status", args)
        assert r.structured_content["status"] == "rejected"
        with pytest.raises(ToolError, match="Approval required"):               # used up
            await c.call_tool("update_batch_status", args)
    events = [(r["event"], r.get("approval_id")) for r in log_of(gw.root)]
    executed = [r for r in log_of(gw.root) if r["event"] == "executed"][0]
    assert executed["signature"]["printed_name"] == "Dr. A. Reviewer" and executed["signature"]["meaning"] == "approved"
    assert events.count(("executed", apr.id)) == 1
    assert AuditLog(gw.root / "audit.jsonl").verify().ok


async def test_role_hides_and_denies(gw):
    g = gw(Policy(roles={"qc": {"allow": ["get_*"]}}))
    async with Client(g) as c:
        assert [t.name for t in await c.list_tools()] == ["get_batch"]
        with pytest.raises(ToolError, match="Denied"):
            await c.call_tool("update_batch_status", {"batch_id": "B1", "status": "x"})
    assert log_of(gw.root)[-1]["event"] == "denied"


async def test_no_identity_denied(gw, monkeypatch):
    monkeypatch.delenv("OPENGXP_USER")
    async with Client(gw()) as c:
        with pytest.raises(ToolError, match="No identified user"):
            await c.call_tool("get_batch", {"batch_id": "B1"})


async def test_monitor_mode_logs_but_allows(gw):
    g = gw(Policy(mode="monitor", roles={"qc": {"allow": ["get_*"]}}))
    async with Client(g) as c:
        await c.call_tool("update_batch_status", {"batch_id": "B1", "status": "x"})
    ev = [r["event"] for r in log_of(gw.root)]
    assert ev == ["would_deny", "executed"]          # enforcement would have stopped at the denial
    g2 = gw(Policy(mode="monitor"))
    async with Client(g2) as c:
        await c.call_tool("update_batch_status", {"batch_id": "B2", "status": "x"})
    assert [r["event"] for r in log_of(gw.root)][-2:] == ["would_require_approval", "executed"]


async def test_validated_only_role_and_version_pinning(gw, tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text("tools:\n  cmc_impurity_thresholds:\n    intended_use: Q3A/B thresholds for specifications\n"
                   "    risk: medium\n    status: validated\n    version: '1.0'\n    evidence: ['tests/cmc/test_impurity_thresholds.py::*']\n"
                   "    validated_by: qa_lead\n    validated_on: '2026-11-18'\n"
                   "  cmc_residual_solvent_limit:\n    status: validated\n    version: '9.9'\n")
    g = gw(Policy(validated_only_roles=["qc"]), Registry(reg), target="opengxp")
    async with Client(g) as c:
        names = {t.name for t in await c.list_tools()}
        assert names == {"cmc_impurity_thresholds"}                              # 9.9 != running 1.0; others unvalidated
        r = await c.call_tool("cmc_impurity_thresholds", {"product_type": "drug_substance", "max_daily_dose": 1, "dose_unit": "g"})
        assert r.structured_content["thresholds"]
        with pytest.raises(ToolError, match="not registered as validated"):
            await c.call_tool("cmc_residual_solvent_limit", {"solvents": ["DCM"]})
    assert log_of(gw.root)[-2]["validation_status"] == "validated"


async def test_redaction(gw):
    g = gw(Policy(redact_args=["batch_*"]))
    async with Client(g) as c:
        await c.call_tool("get_batch", {"batch_id": "SECRET-1"})
    rec = log_of(gw.root)[-1]
    assert "SECRET-1" not in json.dumps(rec["inputs"]) and "redacted_sha256" in rec["inputs"]["batch_id"]


async def test_proxy_in_front_of_separate_server_process(gw):
    target = f"{sys.executable} {Path(__file__).with_name('target_server.py')}"
    g = gw(target=target)
    async with Client(g) as c:
        assert {t.name for t in await c.list_tools()} == {"get_batch", "update_batch_status"}
        r = await c.call_tool("get_batch", {"batch_id": "P9"})
        assert r.structured_content["batch_id"] == "P9"
        with pytest.raises(ToolError, match="Approval required"):
            await c.call_tool("update_batch_status", {"batch_id": "P9", "status": "hold"})
    assert [r["event"] for r in log_of(gw.root)] == ["executed", "approval_required"]


# ---- reports and CLI -------------------------------------------------------------------------------------
async def test_reports_and_cli(gw, capsys):
    async with Client(gw()) as c:
        await c.call_tool("get_batch", {"batch_id": "B1"})
    log = AuditLog(gw.root / "audit.jsonl")
    md = audit_report(log)
    assert "Chain verification: PASSED" in md and "get_batch" in md and "Reviewed by" in md
    assert audit_report(log, fmt="csv").splitlines()[0].startswith("seq,timestamp_utc,event")
    assert main(["--dir", str(gw.root), "verify"]) == 0
    assert "PASSED" in capsys.readouterr().out
    p = gw.root / "audit.jsonl"
    p.write_text(p.read_text().replace("B1", "B2"))
    assert main(["--dir", str(gw.root), "verify"]) == 1
    assert "FAILED" in audit_report(log)


def test_validation_summary_with_junit(tmp_path):
    reg = tmp_path / "r.yaml"
    reg.write_text("tools:\n  t1:\n    status: validated\n    intended_use: x\n    risk: low\n    evidence: ['tests/a.py::*']\n")
    junit = tmp_path / "j.xml"
    junit.write_text('<testsuites><testsuite><testcase classname="tests.a" name="test_ok"/>'
                     '<testcase classname="tests.a" name="test_bad"><failure/></testcase></testsuite></testsuites>')
    md = validation_summary(Registry(reg), [("t1", None, None), ("t2", {"validated_against": "ICH examples"}, "1.0")], junit)
    assert "1 passed, 1 failed" in md and "NOT validated: tests failing" in md
    assert "self_declared" in md and "ICH examples" in md


def test_multiple_roles_any_allows():
    from opengxp.gateway.policy import decide_roles
    p = Policy(roles={"qc": RoleRule(allow=["cmc_*"]), "formulation": RoleRule(allow=["ref_*"])},
               validated_only_roles=["qc"])
    assert decide_roles(p, ("qc", "formulation"), True, "ref_check_excipients", True, False).allowed
    assert decide_roles(p, ("qc", "formulation"), True, "cmc_x", True, True).allowed
    d = decide_roles(p, ("qc", "formulation"), True, "sdtm_validate", True, True)
    assert not d.allowed and "'qc'" in d.reason
    assert not decide_roles(p, (), True, "ref_check_excipients", True, True).allowed
