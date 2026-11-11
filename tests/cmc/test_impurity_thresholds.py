"""Ground truth = the worked examples printed in ICH Q3A(R2) and Q3B(R2) Attachment 2,
plus every row of the Attachment 1 tables."""

import pytest

from opengxp.cmc.impurity_thresholds import ImpurityObservation as Imp
from opengxp.cmc.impurity_thresholds import assess_impurity_thresholds as run
from opengxp.core import GxPInputError


def rows(res):
    return {r.name: r for r in res.impurities}


def th(res):
    return {t.kind: t.binding_text for t in res.thresholds}


# ---- Q3A(R2) Attachment 2 -------------------------------------------------------------------
# Example 1: 0.5 g MDD. (raw, reported, TDI mg rounded, identification exceeded, qualification exceeded)
Q3A_EX1 = [(0.044, None, "0.2", False, False), (0.0963, "0.10", "0.5", False, False),
           (0.12, "0.12", "0.6", True, False), (0.1649, "0.16", "0.8", True, True)]
# Example 2: 0.8 g MDD; qualification threshold is 1.0 mg TDI.
Q3A_EX2 = [(0.066, "0.07", "0.6", False, False), (0.124, "0.12", "1.0", True, False),
           (0.143, "0.14", "1.1", True, True)]


@pytest.mark.parametrize("dose,cases,expected", [
    (0.5, Q3A_EX1, {"reporting": "0.05%", "identification": "0.10%", "qualification": "0.15%"}),
    (0.8, Q3A_EX2, {"reporting": "0.05%", "identification": "0.10%", "qualification": "1.0 mg TDI"}),
])
def test_q3a_attachment2(dose, cases, expected):
    res = run("drug_substance", dose, "g", [Imp(name=str(c[0]), level_percent=c[0]) for c in cases])
    assert th(res) == expected
    r = rows(res)
    for raw, reported, tdi, ident, qual in cases:
        a = r[str(raw)]
        assert a.reported_percent == reported
        assert a.identification.exceeded is ident
        assert a.qualification.exceeded is qual
        if a.qualification.threshold.endswith("TDI"):
            assert a.qualification.compared_value == f"{tdi} mg"


# ---- Q3B(R2) Attachment 2 -------------------------------------------------------------------
# Example 1: 50 mg MDD -> reporting 0.1%, identification 0.2%, qualification 200 µg TDI.
Q3B_EX1 = [(0.04, None, "20", False, False), (0.2143, "0.2", "100", False, False),
           (0.349, "0.3", "150", True, False), (0.550, "0.6", "300", True, True)]
# Example 2: 1.9 g MDD -> reporting 0.05%, identification 2 mg TDI, qualification 3 mg TDI.
Q3B_EX2 = [(0.049, None, "1", False, False), (0.079, "0.08", "2", False, False),
           (0.183, "0.18", "3", True, False), (0.192, "0.19", "4", True, True)]


def test_q3b_example1_50mg():
    res = run("drug_product", 50, "mg", [Imp(name=str(c[0]), level_percent=c[0]) for c in Q3B_EX1])
    assert th(res) == {"reporting": "0.1%", "identification": "0.2%", "qualification": "200 µg TDI"}
    r = rows(res)
    for raw, reported, tdi_ug, ident, qual in Q3B_EX1:
        a = r[str(raw)]
        assert a.reported_percent == reported
        assert a.qualification.compared_value == f"{tdi_ug} µg"
        assert (a.identification.exceeded, a.qualification.exceeded) == (ident, qual)


def test_q3b_example2_1_9g():
    res = run("drug_product", 1.9, "g", [Imp(name=str(c[0]), level_percent=c[0]) for c in Q3B_EX2])
    assert th(res) == {"reporting": "0.05%", "identification": "2 mg TDI", "qualification": "3 mg TDI"}
    r = rows(res)
    for raw, reported, tdi_mg, ident, qual in Q3B_EX2:
        a = r[str(raw)]
        assert a.reported_percent == reported
        assert a.qualification.compared_value == f"{tdi_mg} mg"
        assert (a.identification.exceeded, a.qualification.exceeded) == (ident, qual)


# ---- Attachment 1 tables, including band edges ------------------------------------------------
@pytest.mark.parametrize("mg,rep,ident,qual", [
    (100, "0.05%", "0.10%", "0.15%"),             # % limb binds at low dose
    (2000, "0.05%", "1.0 mg TDI", "1.0 mg TDI"),  # 0.10% of 2 g = 2 mg > 1.0 mg
    (2001, "0.03%", "0.05%", "0.05%"),            # > 2 g/day band
])
def test_q3a_table(mg, rep, ident, qual):
    assert th(run("drug_substance", mg, "mg")) == {"reporting": rep, "identification": ident, "qualification": qual}


@pytest.mark.parametrize("mg,ident,qual", [
    (0.5, "1.0%", "1.0%"),          # <1 mg: 1.0% of 0.5 mg = 5 µg = TDI (tie -> % limb)
    (1, "0.5%", "1.0%"),            # 1 mg falls in the 1-10 mg band: 0.5% = 5 µg < 20 µg
    (10, "20 µg TDI", "0.5%"),      # 0.5% of 10 mg = 50 µg > 20 µg; qual 10-100 mg band
    (100, "0.2%", "200 µg TDI"),    # 0.2% = 200 µg < 2 mg; qual 0.5% = 500 µg > 200 µg
    (2000, "2 mg TDI", "3 mg TDI"),
    (2500, "0.10%", "0.15%"),
])
def test_q3b_table(mg, ident, qual):
    t = th(run("drug_product", mg, "mg"))
    assert (t["identification"], t["qualification"]) == (ident, qual)


def test_q3b_reporting_band():
    assert th(run("drug_product", 1, "g"))["reporting"] == "0.1%"
    assert th(run("drug_product", 1.001, "g"))["reporting"] == "0.05%"


def test_q3b_scope_warning():
    res = run("drug_product", 100, "mg", [Imp(name="P1", level_percent=0.3, is_degradant=False)])
    assert any(f.code == "Q3B_SCOPE" for f in res.findings)


def test_bad_dose():
    with pytest.raises(GxPInputError):
        run("drug_substance", 0, "mg")


def test_provenance_deterministic():
    a = run("drug_substance", 500, "mg", [Imp(name="A", level_percent=0.12)])
    b = run("drug_substance", 0.5, "g", [Imp(name="A", level_percent=0.12)])
    assert a.provenance.inputs_hash != b.provenance.inputs_hash  # inputs differ textually
    c = run("drug_substance", 500, "mg", [Imp(name="A", level_percent=0.12)])
    assert a.provenance.inputs_hash == c.provenance.inputs_hash
