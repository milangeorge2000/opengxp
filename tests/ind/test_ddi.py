"""ICH M12 basic methods, basic kinetic induction model and mechanistic static AUCR (Table 3, Table 6).
Expected values are hand-computed from the M12 equations."""

import pytest

from opengxp.core import GxPInputError
from opengxp.ind.ddi import EnzymeData as E
from opengxp.ind.ddi import InductionDonor as Donor
from opengxp.ind.ddi import Perpetrator, assess_ddi_risk
from opengxp.ind.ddi import TransporterData as T

# Cmax 2 µM, fu 0.05 -> Cmax,u 0.1 µM; 100 mg / 400 g/mol = 250 µmol -> Dose/250 mL = 1000 µM
P = Perpetrator(cmax_total_uM=2, fu_p=0.05, dose_mg=100, mw_g_per_mol=400, blood_to_plasma=1)
INLET = 0.05 * (2 + 0.1 * 250 / (97 / 60))      # 0.8732 µM
IG = 0.1 * 250 / (18 / 60)                       # 83.33 µM


def check(r, target, method_prefix="basic", mechanism=None):
    return next(c for c in r.checks if c.target == target and c.method.startswith(method_prefix)
                and (mechanism is None or c.mechanism == mechanism))


def concl(r, target, mech):
    return next(c for c in r.conclusions if c.target == target and c.mechanism == mech)


def test_derived_concentrations():
    r = assess_ddi_risk(P, [E(enzyme="CYP2D6", ki_u_uM=10)])
    assert r.cmax_u_uM == 0.1 and r.dose_conc_gut_uM == 1000
    assert r.cmax_inlet_u_uM == pytest.approx(INLET, rel=1e-3) and r.i_gut_uM == pytest.approx(IG, rel=1e-3)


@pytest.mark.parametrize("ki,excluded", [(10, True), (5.01, True), (5, False), (2, False)])
def test_reversible_cutoff_is_strict(ki, excluded):          # Ki,u > 50 x Cmax,u  <=>  ratio < 0.02
    r = assess_ddi_risk(P, [E(enzyme="CYP2C9", ki_u_uM=ki)])
    assert check(r, "CYP2C9").risk_excluded is excluded


def test_gut_cyp3a_can_fail_when_systemic_passes():
    r = assess_ddi_risk(P, [E(enzyme="CYP3A", ki_u_uM=20)])
    assert check(r, "CYP3A").risk_excluded is True              # 0.1/20 = 0.005
    g = check(r, "CYP3A (intestinal)")
    assert g.value == 50 and g.risk_excluded is False           # 1000/20 = 50 >= 10
    assert concl(r, "CYP3A", "inhibition").risk_excluded is False
    assert any(f.code == "DDI_RISK_NOT_EXCLUDED" for f in r.findings)


def test_no_gut_check_for_parenteral():
    r = assess_ddi_risk(P.model_copy(update={"route": "parenteral"}), [E(enzyme="CYP3A", ki_u_uM=20)])
    assert r.dose_conc_gut_uM is None and concl(r, "CYP3A", "inhibition").risk_excluded is True


def test_tdi_uses_5x_cmax_and_table6_kdeg():
    r = assess_ddi_risk(P, [E(enzyme="CYP3A", KI_u_uM=5, kinact_per_min=0.05)])
    kobs = 0.05 * 0.5 / (5 + 0.5)
    c = check(r, "CYP3A", mechanism="time_dependent_inhibition")
    assert c.value == pytest.approx((kobs + 0.00032) / 0.00032, rel=1e-3) and c.risk_excluded is False
    weak = assess_ddi_risk(P, [E(enzyme="CYP2C9", KI_u_uM=500, kinact_per_min=0.0001)])
    assert check(weak, "CYP2C9", mechanism="time_dependent_inhibition").risk_excluded is True  # ratio 1.009
    with pytest.raises(GxPInputError, match="both KI_u_uM and kinact"):
        assess_ddi_risk(P, [E(enzyme="CYP2C9", KI_u_uM=5)])


def test_fu_floor():
    r = assess_ddi_risk(P.model_copy(update={"fu_p": 0.001}), [E(enzyme="CYP2D6", ki_u_uM=10)])
    assert r.cmax_u_uM == 0.02 and any("fu,p = 0.01" in a for a in r.assumptions)
    r2 = assess_ddi_risk(P.model_copy(update={"fu_p": 0.001, "fu_p_reliable_below_1pct": True}), [E(enzyme="CYP2D6", ki_u_uM=10)])
    assert r2.cmax_u_uM == 0.002


def test_rb_default_flagged():
    r = assess_ddi_risk(P.model_copy(update={"blood_to_plasma": None}), [E(enzyme="CYP2D6", ki_u_uM=10)])
    assert any(f.code == "RB_DEFAULTED" for f in r.findings)


@pytest.mark.parametrize("name,ic50,excluded", [
    ("OATP1B1", 10, True), ("OATP1B3", 5, False),             # inlet 0.873: < 0.1 x IC50
    ("P-gp", 200, True), ("BCRP", 50, False),                 # 1000/IC50 < 10
    ("OAT1", 2, True), ("OCT2", 1, False),                    # 0.1/IC50 < 0.1
    ("MATE1", 10, True), ("MATE2-K", 5, False),               # 0.1/IC50 < 0.02
])
def test_transporter_table1(name, ic50, excluded):
    r = assess_ddi_risk(P, transporters=[T(transporter=name, ic50_u_uM=ic50)])
    assert r.checks[0].risk_excluded is excluded


def test_pgp_parenteral_uses_cmax():
    r = assess_ddi_risk(P.model_copy(update={"route": "parenteral"}), transporters=[T(transporter="P-gp", ic50_u_uM=10)])
    assert r.checks[0].value == 0.01 and r.checks[0].risk_excluded is True


def test_induction_fold_change_below_50x_cmax():
    pos = Donor(donor_id="D1", concentrations_uM=[0.5, 2, 4], fold_change=[1.2, 1.8, 2.5])
    r = assess_ddi_risk(P, [E(enzyme="CYP3A", induction_donors=[pos, pos, pos])])
    assert check(r, "CYP3A", "basic mRNA").risk_excluded is False
    high = Donor(donor_id="D2", concentrations_uM=[1, 3, 10], fold_change=[1.1, 1.5, 4.0])   # 4-fold only at 10 µM >= 5
    r = assess_ddi_risk(P, [E(enzyme="CYP3A", induction_donors=[high] * 3)])
    assert check(r, "CYP3A", "basic mRNA").risk_excluded is True
    flat = Donor(donor_id="D3", concentrations_uM=[0.5, 2, 4], fold_change=[2.2, 2.1, 2.0], concentration_dependent=False)
    r = assess_ddi_risk(P, [E(enzyme="CYP3A", induction_donors=[flat])])
    assert check(r, "CYP3A", "basic mRNA").risk_excluded is True
    assert any(f.code == "FEWER_THAN_3_DONORS" for f in r.findings)


def test_induction_inconclusive_weak_positive_control():
    d = Donor(donor_id="D1", concentrations_uM=[1, 4], fold_change=[1.2, 1.5], positive_control_fold=3)  # 25% of control
    r = assess_ddi_risk(P, [E(enzyme="CYP2B6", induction_donors=[d] * 3)])
    assert check(r, "CYP2B6", "basic mRNA").risk_excluded is None
    assert concl(r, "CYP2B6", "induction").risk_excluded is None


@pytest.mark.parametrize("ec50,R,excluded", [(5, 1 / (1 + 4 * 1 / 6), False), (50, 1 / (1 + 4 / 51), True)])
def test_basic_kinetic_model(ec50, R, excluded):
    pos = Donor(donor_id="D1", concentrations_uM=[0.5, 2, 4], fold_change=[1.2, 1.8, 2.5])
    r = assess_ddi_risk(P, [E(enzyme="CYP3A", induction_donors=[pos] * 3, emax=4, ec50_u_uM=ec50)])
    c = check(r, "CYP3A", "basic kinetic")
    assert c.value == pytest.approx(R, rel=1e-3) and c.risk_excluded is excluded
    assert concl(r, "CYP3A", "induction").risk_excluded is excluded


def test_cyp2d6_induction_ignored():
    d = Donor(donor_id="D1", concentrations_uM=[1, 4], fold_change=[2, 3])
    r = assess_ddi_risk(P, [E(enzyme="CYP2D6", induction_donors=[d] * 3)])
    assert any(f.code == "CYP2D6_NOT_INDUCIBLE" for f in r.findings) and not r.conclusions


@pytest.mark.parametrize("fm,excluded", [(0.9, False), (0.2, True)])
def test_mechanistic_static_rescues_basic_fail(fm, excluded):
    r = assess_ddi_risk(P, [E(enzyme="CYP2C9", ki_u_uM=2, substrate_fm=fm)])
    ah = 1 / (1 + INLET / 2)
    m = check(r, "CYP2C9", "mechanistic")
    assert m.value == pytest.approx(1 / (ah * fm + 1 - fm), rel=1e-3)
    c = concl(r, "CYP2C9", "inhibition")
    assert c.risk_excluded is excluded
    assert c.decided_by == ("mechanistic static model" if excluded else "basic method and mechanistic static model")


def test_mechanistic_cyp3a_gut_and_separate_inhibition_induction():
    e = E(enzyme="CYP3A", ki_u_uM=20, emax=4, ec50_u_uM=5, substrate_fm=0.9, substrate_fg=0.5)
    r = assess_ddi_risk(P, [e])
    methods = {c.method for c in r.checks if c.method.startswith("mechanistic")}
    assert methods == {"mechanistic static AUCR (combined)", "mechanistic static AUCR (inhibition only)",
                       "mechanistic static AUCR (induction only)"}
    ag, ah = 1 / (1 + IG / 20), 1 / (1 + INLET / 20)
    inhib = 1 / (ag * 0.5 + 0.5) * 1 / (ah * 0.9 + 0.1)
    assert check(r, "CYP3A", "mechanistic static AUCR (inhibition").value == pytest.approx(inhib, rel=1e-3)
    cg, ch = 1 + 4 * IG / (IG + 5), 1 + 4 * INLET / (INLET + 5)
    ind = 1 / (cg * 0.5 + 0.5) * 1 / (ch * 0.9 + 0.1)
    assert check(r, "CYP3A", "mechanistic static AUCR (induction").value == pytest.approx(ind, rel=1e-3)


def test_ugt_uses_cyp_criterion_with_note():
    r = assess_ddi_risk(P, [E(enzyme="UGT1A1", ki_u_uM=10)])
    c = check(r, "UGT1A1")
    assert c.risk_excluded is True and c.section == "M12 2.1.3" and "UGT" in c.detail


def test_needs_some_data():
    with pytest.raises(GxPInputError):
        assess_ddi_risk(P)
