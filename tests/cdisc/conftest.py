import json
from pathlib import Path

import pytest


def issue(cid, ds, row, subj, vars_, vals, msg):
    return {"core_id": cid, "message": msg, "executability": "fully executable", "dataset": ds, "USUBJID": subj,
            "row": row, "SEQ": row, "variables": vars_, "values": vals}


def make_report(n_ae=4, n_dm=2, with_error=True):
    det = [issue("CORE-000001", "ae.xpt", i + 1, f"01-70{i}", ["AESTDTC", "AEENDTC"], ["2024-03-05", "2024-03-01"],
                 "AESTDTC is after AEENDTC") for i in range(n_ae)]
    det += [issue("CORE-000123", "dm.xpt", i + 1, f"01-80{i}", ["ARMCD"], [None], "ARMCD is null") for i in range(n_dm)]
    if with_error:
        det.append({"core_id": "CORE-000999", "message": "Rule failed - KeyError VISITNUM", "executability": "fully executable",
                    "dataset": "vs.xpt", "USUBJID": "", "row": "", "SEQ": "", "variables": "", "values": "KeyError"})
    return {
        "Conformance_Details": {"CORE_Engine_Version": "1.0.0", "Standard": "SDTMIG", "Version": "V3.4"},
        "Dataset_Details": [{"filename": "ae.xpt", "label": "Adverse Events", "length": 120},
                            {"filename": "dm.xpt", "label": "Demographics", "length": 50}],
        "Issue_Summary": [],
        "Issue_Details": det,
        "Rules_Report": [
            {"core_id": "CORE-000001", "version": "1", "cdisc_rule_id": "CG0001", "fda_rule_id": "SD1022", "message": "AESTDTC is after AEENDTC", "status": "ISSUE REPORTED"},
            {"core_id": "CORE-000123", "version": "1", "cdisc_rule_id": "CG0123", "fda_rule_id": "", "message": "ARMCD is null", "status": "ISSUE REPORTED"},
            {"core_id": "CORE-000500", "version": "1", "cdisc_rule_id": "CG0500", "fda_rule_id": "", "message": "n/a", "status": "SKIPPED"},
            {"core_id": "CORE-000999", "version": "1", "cdisc_rule_id": "CG0999", "fda_rule_id": "", "message": "x", "status": "EXECUTION ERROR"},
        ],
    }


@pytest.fixture
def core_env(tmp_path, monkeypatch):
    data = tmp_path / "sdtm"
    data.mkdir()
    for n in ("ae.xpt", "dm.xpt", "define.xml"):
        (data / n).write_bytes(b"x")
    rpt = tmp_path / "report.json"
    rpt.write_text(json.dumps(make_report()))
    monkeypatch.setenv("OPENGXP_CORE", str(Path(__file__).with_name("fake_core.py")))
    monkeypatch.setenv("OPENGXP_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("FAKE_CORE_REPORT", str(rpt))
    monkeypatch.setenv("FAKE_CORE_ARGS", str(tmp_path / "args.json"))
    return {"data": data, "report": rpt, "args": tmp_path / "args.json"}
