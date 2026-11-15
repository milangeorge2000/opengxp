"""Ground truth = ICH Q3C(R9) Tables 1-4 and the two acetonitrile examples in section 3.3."""

import pytest

from opengxp.cmc.residual_solvents import ComponentSolvent as C
from opengxp.cmc.residual_solvents import assess_residual_solvents as run
from opengxp.cmc.residual_solvents import resolve_solvent, solvent_table
from opengxp.core import GxPInputError


def test_table_counts():
    by = {}
    for s in solvent_table():
        by[s.solvent_class] = by.get(s.solvent_class, 0) + 1
    assert by == {"1": 5, "2": 31, "3": 27, "no_pde": 9}


def test_class2_option1_is_pde_over_10g_to_nearest_10ppm():
    # Q3C: Option 1 ppm = 1000 x PDE / 10 g; concentrations given to the nearest 10 ppm
    for s in solvent_table():
        if s.solvent_class == "2":
            assert abs(s.option1_limit_ppm - 100 * s.pde_mg_per_day) <= 5, s.name


@pytest.mark.parametrize("q,name", [
    ("DCM", "Dichloromethane"), ("methylene chloride", "Dichloromethane"), ("75-09-2", "Dichloromethane"),
    ("IPA", "2-Propanol"), ("thf", "Tetrahydrofuran"), ("N-methyl-2-pyrrolidone", "N-Methylpyrrolidone"),
    ("MTBE", "tert-Butylmethyl ether"), ("2-MeTHF", "2-Methyltetrahydrofuran"),
])
def test_resolve(q, name):
    assert resolve_solvent(q).name == name


def test_unknown_solvent_suggests():
    with pytest.raises(GxPInputError, match="Did you mean.*Dichloromethane"):
        resolve_solvent("dichloromethan")


@pytest.mark.parametrize("q,ppm", [("benzene", 2), ("carbon tetrachloride", 4), ("1,2-dichloroethane", 5),
                                   ("1,1-dichloroethene", 8), ("1,1,1-trichloroethane", 1500)])
def test_class1_limits(q, ppm):
    r = run([q])
    assert r.solvents[0].option1_limit_ppm == ppm
    assert any(f.code == "CLASS1_SOLVENT" for f in r.findings)


def test_acetonitrile_example1_passes_option2():
    comps = [C(name="Drug substance", mass_g=0.3, solvent_ppm={"acetonitrile": 800}),
             C(name="Excipient 1", mass_g=0.9, solvent_ppm={"acetonitrile": 400}),
             C(name="Excipient 2", mass_g=3.8, solvent_ppm={"acetonitrile": 800})]
    a = run(["acetonitrile"], option="option2", daily_dose_g=5.0, components=comps).solvents[0]
    assert a.option1_limit_ppm == 410
    assert a.option2_total_mg_per_day == pytest.approx(3.64)
    assert a.option2_product_ppm == pytest.approx(728)
    assert a.passes_option2 is True


def test_acetonitrile_example2_fails_option2():
    comps = [C(name="Drug substance", mass_g=0.3, solvent_ppm={"acetonitrile": 800}),
             C(name="Excipient 1", mass_g=0.9, solvent_ppm={"acetonitrile": 2000}),
             C(name="Excipient 2", mass_g=3.8, solvent_ppm={"acetonitrile": 800})]
    r = run(["acetonitrile"], option="option2", daily_dose_g=5.0, components=comps)
    a = r.solvents[0]
    assert a.option2_total_mg_per_day == pytest.approx(5.08)
    assert a.option2_product_ppm == pytest.approx(1016)
    assert a.passes_option2 is False
    assert any(f.code == "ABOVE_PDE" for f in r.findings)


def test_option2_limit_formula():
    a = run(["toluene"], option="option2", daily_dose_g=2.0).solvents[0]
    assert a.option2_limit_ppm == pytest.approx(1000 * 8.9 / 2.0)


def test_option2_needs_dose():
    with pytest.raises(GxPInputError, match="daily_dose_g"):
        run(["toluene"], option="option2")


def test_class3_default():
    a = run(["ethanol"]).solvents[0]
    assert (a.solvent_class, a.pde_mg_per_day, a.option1_limit_ppm) == ("3", 50.0, 5000.0)


def test_measured_option1_flag():
    r = run(["methanol"], measured_ppm={"methanol": 3100})
    assert r.solvents[0].passes_option1 is False
    assert any(f.code == "ABOVE_OPTION1" for f in r.findings)
