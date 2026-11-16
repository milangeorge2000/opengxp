"""Ground truth = ICH Q3D(R2) Tables A.2.1, A.2.2, A.5.1 and the Appendix 4 worked example
(solid oral product, 2.5 g/day, 9 components, Table A.4.1)."""

import pytest

from opengxp.cmc.elemental_impurities import PDE, Component, assess_elemental_impurities as run, pde
from opengxp.core import GxPInputError

# Table A.2.2 (Option 1, µg/g) = PDE / 10 g
A22 = {"Cd": (0.5, 0.2, 0.3), "Pb": (0.5, 0.5, 0.5), "As": (1.5, 1.5, 0.2), "Hg": (3, 0.3, 0.1),
       "Co": (5, 0.5, 0.3), "V": (10, 1, 0.1), "Ni": (20, 2, 0.6), "Tl": (0.8, 0.8, 0.8), "Au": (30, 30, 0.3),
       "Pd": (10, 1, 0.1), "Ir": (10, 1, 0.1), "Os": (10, 1, 0.1), "Rh": (10, 1, 0.1), "Ru": (10, 1, 0.1),
       "Se": (15, 8, 13), "Ag": (15, 1.5, 0.7), "Pt": (10, 1, 0.1), "Li": (55, 25, 2.5), "Sb": (120, 9, 2),
       "Ba": (140, 70, 30), "Mo": (300, 150, 1), "Cu": (300, 30, 3), "Sn": (600, 60, 6), "Cr": (1100, 110, 0.3)}


@pytest.mark.parametrize("sym", list(A22))
def test_table_a22_option1(sym):
    for route, expected in zip(("oral", "parenteral", "inhalation"), A22[sym]):
        r = run(route, 2.0, "option1", elements=[sym])
        assert r.elements[0].permitted_conc_ug_per_g == pytest.approx(expected), (sym, route)


# Table A.4.1 component daily intakes (g) -> 2.5 g product
MASS = {"Drug Substance": 0.200, "MCC": 1.100, "Lactose": 0.450, "Ca Phosphate": 0.350, "Crospovidone": 0.265,
        "Mg Stearate": 0.035, "HPMC": 0.060, "Titanium Dioxide": 0.025, "Iron Oxide": 0.015}
TARGETS = ["Pb", "As", "Cd", "Hg", "Pd", "V", "Ni"]


def test_example_component_masses_sum_to_2_5g():
    assert sum(MASS.values()) == pytest.approx(2.5)


def test_option2a_example():
    # Table A.4.3: common permitted concentrations at 2.5 g/day
    expected = {"Pb": 2, "As": 6, "Cd": 2, "Hg": 12, "Pd": 40, "V": 40, "Ni": 80}
    r = run("oral", 2.5, "option2a", elements=TARGETS)
    assert {e.symbol: e.permitted_conc_ug_per_g for e in r.elements} == expected


def test_option1_example_max_intake():
    # Table A.4.2: Option 1 concentrations in all components give these maximum daily intakes (µg)
    expected = {"Pb": 1.25, "As": 3.75, "Cd": 1.25, "Hg": 7.5, "Pd": 25, "V": 25, "Ni": 50}
    comps = [Component(name=n, mass_g=m, element_ug_per_g={e: A22[e][0] for e in TARGETS}) for n, m in MASS.items()]
    r = run("oral", 2.5, "option1", components=comps, elements=TARGETS)
    for e in r.elements:
        assert e.total_intake_ug == pytest.approx(expected[e.symbol])
        assert not e.exceeds_pde


# Table A.4.5: an Option 2b set of component concentrations that keeps the product within PDEs (Pd not assessed)
A45 = {"Drug Substance": {"As": 5, "Pd": 500, "Ni": 750},
       "MCC": {"Pb": 0.5, "As": 5, "Cd": 1, "Hg": 5}, "Lactose": {"Pb": 0.5, "As": 5, "Cd": 1, "Hg": 5},
       "Ca Phosphate": {"Pb": 5, "As": 5, "Cd": 5, "Hg": 35, "V": 70, "Ni": 80},
       "Crospovidone": {"Pb": 0.5, "As": 5, "Cd": 1, "Hg": 5},
       "Mg Stearate": {"Pb": 5, "As": 10, "Cd": 5, "Hg": 125, "Ni": 100},
       "HPMC": {"Pb": 2.5, "As": 5, "Cd": 1, "Hg": 5},
       "Titanium Dioxide": {"Pb": 50, "As": 40, "Cd": 10, "Hg": 35, "V": 20},
       "Iron Oxide": {"Pb": 50, "As": 100, "Cd": 50, "Hg": 200, "V": 5000, "Ni": 1200}}


def test_option2b_example_within_pde():
    comps = [Component(name=n, mass_g=MASS[n], element_ug_per_g=c) for n, c in A45.items()]
    r = run("oral", 2.5, "option2b", components=comps, elements=TARGETS)
    by = {e.symbol: e for e in r.elements}
    for sym in ("Pb", "As", "Cd", "Hg", "V", "Ni"):
        assert by[sym].exceeds_pde is False, sym
    assert by["Pb"].total_intake_ug == pytest.approx(4.9825)       # hand-checked: tight against 5 µg PDE
    assert by["Pb"].drivers[0].component == "Ca Phosphate"
    assert by["Pb"].above_control_threshold is True


def test_cutaneous_co_ctcl_binds():
    # Table A.5.1 footnote 4: Co, 1 g/day -> PDE-derived 50 µg/g exceeds CTCL 35 µg/g, so 35 applies
    r = run("cutaneous", 1.0, "option2a", elements=["Co"])
    assert r.elements[0].permitted_conc_ug_per_g == 35


def test_cutaneous_pde_values():
    assert pde("Ir", "cutaneous") == pde("Pd", "cutaneous") == 100
    assert pde("Cd", "cutaneous") == 20 and pde("Mo", "cutaneous") == 15000


def test_default_risk_assessment_sets():
    oral = {e.symbol for e in run("oral", 1.0).elements}
    assert oral == {"Cd", "Pb", "As", "Hg", "Co", "V", "Ni"}
    inh = {e.symbol for e in run("inhalation", 1.0).elements}
    assert inh == {"Cd", "Pb", "As", "Hg", "Co", "V", "Ni", "Li", "Sb", "Ba", "Mo", "Cu", "Sn", "Cr"}
    par = {e.symbol for e in run("parenteral", 1.0).elements}
    assert par == {"Cd", "Pb", "As", "Hg", "Co", "V", "Ni", "Li", "Sb", "Cu"}
    added = {e.symbol for e in run("oral", 1.0, intentionally_added=["palladium"]).elements}
    assert "Pd" in added


def test_all_24_elements_present():
    assert len(PDE) == 24


def test_unknown_element():
    with pytest.raises(GxPInputError, match="Unknown element"):
        run("oral", 1.0, elements=["Xx"])


def test_no_pde_element_info():
    r = run("oral", 1.0, components=[Component(name="API", mass_g=1, element_ug_per_g={"Fe": 50})])
    assert r.elements_without_pde == ["Fe"]
