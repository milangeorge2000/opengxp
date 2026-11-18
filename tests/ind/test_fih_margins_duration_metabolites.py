"""FDA 2005 MRSD (Table 1, Appendix C example), ICH S9, E14/S7B Q&A margins, M3(R2) Tables 1-2, MIST."""

import pytest

from opengxp.core import GxPInputError
from opengxp.ind.fih import ToxStudy as S
from opengxp.ind.fih import first_in_human_dose as fih
from opengxp.ind.margins import ExposureInput, HergInput, safety_margins
from opengxp.ind.metabolites import MetaboliteInput as M
from opengxp.ind.metabolites import metabolite_coverage
from opengxp.ind.tox_duration import tox_duration


# ---- first-in-human ------------------------------------------------------------------------------
def test_appendix_c_rabbit_example():
    std = fih([S(species="rabbit", dose_mg_per_kg=25)])
    assert std.per_species[0].hed_mg_per_kg == pytest.approx(8.06, abs=0.01)          # 25 / 3.1 = 8.1
    wt = fih([S(species="rabbit", dose_mg_per_kg=25, body_weight_kg=4.0)])
    assert wt.per_species[0].hed_mg_per_kg == pytest.approx(10.2, abs=0.1)             # 25 x (4/60)^0.33 = 10
    assert any(f.code == "WEIGHT_FORMULA_USED" for f in wt.findings)


def test_most_sensitive_species_and_mrsd():
    r = fih([S(species="rat", dose_mg_per_kg=50, study="TOX-1"), S(species="Beagle", dose_mg_per_kg=10)])
    assert {s.species: s.hed_mg_per_kg for s in r.per_species} == {"rat": pytest.approx(8.065, abs=1e-3),
                                                                      "dog": pytest.approx(5.556, abs=1e-3)}
    assert r.selected_species == "dog" and r.safety_factor == 10
    assert r.mrsd_mg_per_kg == pytest.approx(0.5556, abs=1e-4) and r.mrsd_mg == pytest.approx(33.33, abs=0.01)
    assert r.per_species[0].study == "TOX-1"


def test_species_override_needs_justification_and_warns():
    st = [S(species="rat", dose_mg_per_kg=50), S(species="cynomolgus", dose_mg_per_kg=40)]
    with pytest.raises(GxPInputError, match="species_justification"):
        fih(st, most_appropriate_species="monkey")
    r = fih(st, most_appropriate_species="monkey", species_justification="Class experience (antisense)")
    assert r.selected_species == "monkey" and r.mrsd_mg_per_kg == pytest.approx(40 / 3.1 / 10, rel=1e-3) and any(f.code == "NOT_MOST_SENSITIVE" for f in r.findings)


def test_safety_factor_and_scaling_rules():
    st = [S(species="dog", dose_mg_per_kg=10)]
    with pytest.raises(GxPInputError, match="below 10"):
        fih(st, safety_factor=5)
    assert fih(st, safety_factor=5, safety_factor_justification="Well-characterised class").mrsd_mg_per_kg == pytest.approx(1.111, abs=1e-3)
    with pytest.raises(GxPInputError, match="scaling_justification"):
        fih(st, scaling="mg_per_kg")
    assert fih(st, scaling="mg_per_kg", scaling_justification="V.B met").per_species[0].hed_mg_per_kg == 10


def test_endpoint_and_species_validation():
    with pytest.raises(GxPInputError, match="NOAEL"):
        fih([S(species="dog", dose_mg_per_kg=3, endpoint="HNSTD")])
    with pytest.raises(GxPInputError, match="not in FDA 2005 Table 1"):
        fih([S(species="pig", dose_mg_per_kg=3)])
    assert fih([S(species="pig", dose_mg_per_kg=3, body_weight_kg=30)]).per_species[0].hed_mg_per_kg > 0


def test_oncology_s9():
    r = fih([S(species="rat", dose_mg_per_kg=60, endpoint="STD10"), S(species="dog", dose_mg_per_kg=3, endpoint="HNSTD")],
            population="oncology")
    rows = {s.species: s.start_dose_mg_per_kg for s in r.per_species}
    assert rows["rat"] == pytest.approx(60 / 6.2 / 10, rel=1e-3) and rows["dog"] == pytest.approx(3 / 1.8 / 6, rel=1e-3)
    assert r.selected_species == "dog" and r.approach == "ICH S9" and r.safety_factor is None
    with pytest.raises(GxPInputError, match="STD10"):
        fih([S(species="rat", dose_mg_per_kg=60, endpoint="HNSTD")], population="oncology")


def test_high_risk_biologic_flag():
    r = fih([S(species="monkey", dose_mg_per_kg=1)], high_risk_biologic=True)
    assert any(f.code == "MABEL_NEEDED" and f.level == "action_required" for f in r.findings)


# ---- safety margins --------------------------------------------------------------------------------
def test_herg_margin_with_fu_floor_and_threshold():
    r = safety_margins(HergInput(ic50_uM=10, cmax_ss_total_uM=1, fu_p=0.001, threshold_fold=45, threshold_source="lab cal 2026"))
    assert r.herg_margin_free == 1000 and r.herg_low_risk is True
    low = safety_margins(HergInput(ic50_uM=1, cmax_ss_total_uM=1, fu_p=0.1, threshold_fold=30))
    assert low.herg_margin_free == 10 and low.herg_low_risk is False
    assert {f.code for f in low.findings} >= {"HERG_MARGIN_BELOW_THRESHOLD", "THRESHOLD_UNSOURCED"}


def test_herg_without_threshold_and_uncertain_binding():
    r = safety_margins(HergInput(ic50_uM=3, cmax_ss_total_uM=0.5, fu_p=0.2, protein_binding_reliable=False))
    assert r.herg_margin_free == 30 and r.herg_margin_total == 6 and r.herg_low_risk is None
    assert {f.code for f in r.findings} >= {"NO_HERG_THRESHOLD", "BINDING_UNCERTAIN"}


def test_exposure_margins():
    r = safety_margins(exposures=[ExposureInput(species="rat", auc_at_noael=500, cmax_at_noael=40),
                                  ExposureInput(species="dog", auc_at_noael=80)], human_auc=100, human_cmax=10)
    assert [(m.species, m.auc_margin, m.cmax_margin) for m in r.exposure_margins] == [("rat", 5, 4), ("dog", 0.8, None)]
    assert r.lowest_auc_margin == 0.8 and any(f.code == "EXPOSURE_BELOW_CLINICAL" for f in r.findings)
    with pytest.raises(GxPInputError, match="human_auc"):
        safety_margins(exposures=[ExposureInput(species="rat", auc_at_noael=1)])


# ---- tox duration ------------------------------------------------------------------------------------
@pytest.mark.parametrize("dur,unit,row,rod,nonrod,eu", [
    (10, "days", "Up to 2 weeks", "2 weeks", "2 weeks", "2 weeks"),
    (2, "weeks", "Up to 2 weeks", "2 weeks", "2 weeks", "2 weeks"),
    (15, "days", "Between 2 weeks and 6 months", "same as the clinical trial (15 days)", None, None),
    (26, "weeks", "Between 2 weeks and 6 months", None, None, None),        # 182 d <= 182.6 d
    (6, "months", "Between 2 weeks and 6 months", None, None, None),
    (7, "months", "> 6 months", "6 months", "9 months", "6 months"),
])
def test_table1_rows(dur, unit, row, rod, nonrod, eu):
    r = tox_duration(dur, unit)
    assert r.table_row == row
    by = {b.region: b for b in r.by_region}
    if rod:
        assert by["US"].rodent == rod
    if nonrod:
        assert by["US"].non_rodent == nonrod and by["JP"].non_rodent == nonrod and by["EU"].non_rodent == eu


@pytest.mark.parametrize("dur,unit,rod,nonrod", [
    (2, "weeks", "1 month", "1 month"), (1, "months", "3 months", "3 months"),
    (3, "months", "6 months", "6 months"), (4, "months", "6 months", "9 months"),
])
def test_table2_marketing(dur, unit, rod, nonrod):
    r = tox_duration(dur, unit, purpose="marketing", regions=["US"])
    assert (r.by_region[0].rodent, r.by_region[0].non_rodent) == (rod, nonrod)


def test_bridging_footnote():
    assert any(f.startswith("b:") for f in tox_duration(4, "months").footnotes)
    assert not any(f.startswith("b:") for f in tox_duration(2, "months").footnotes)


# ---- metabolites -------------------------------------------------------------------------------------
def test_metabolite_coverage_rules():
    r = metabolite_coverage([
        M(name="M1", human_auc=100, percent_of_total=15, animal_auc={"rat": 60, "dog": 20}),      # 0.6 >= 0.5
        M(name="M2", human_auc=100, percent_of_total=60, animal_auc={"dog": 90}),                 # majority: needs > 1
        M(name="M3", human_auc=10, percent_of_total=5),
        M(name="M4", human_auc=50, percent_of_total=25, concern="not_of_toxicological_concern"),
        M(name="M5", human_auc=100, percent_of_total=12, animal_auc={"rat": 50}),                 # exactly 50%: covered
        M(name="M6", human_auc=100, percent_of_total=20),                                        # no animal data
    ], daily_dose_mg=5)
    got = {m.name: m.covered for m in r.metabolites}
    assert got == {"M1": True, "M2": False, "M3": None, "M4": None, "M5": True, "M6": False}
    assert r.metabolites[0].best_species == "rat"
    assert sum(f.code == "METABOLITE_NOT_COVERED" for f in r.findings) == 2
    assert any(f.code == "LOW_DOSE" for f in r.findings)


def test_percent_from_total_and_unique():
    r = metabolite_coverage([M(name="Mx", human_auc=30, animal_auc={"rat": 100}, concern="unique_or_cause_for_concern")],
                            total_drug_related_auc=600)
    assert r.metabolites[0].percent_of_total == 5 and r.metabolites[0].covered is True
    assert any(f.code == "CASE_BY_CASE" for f in r.findings)
    with pytest.raises(GxPInputError, match="percent_of_total"):
        metabolite_coverage([M(name="My", human_auc=1)])
