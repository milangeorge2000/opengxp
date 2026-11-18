import numpy as np
import pytest
from scipy import stats

from opengxp.stability.conditions import storage_conditions as sc
from opengxp.stability.models import StabilityPoint as P
from opengxp.stability.oot import detect_oot

HIST = [P(batch=b, time_months=t, value=100 - 0.2 * t + d) for b, d in [("H1", 0.2), ("H2", -0.1), ("H3", 0.0), ("H4", -0.2)]
        for t in [0, 3, 6, 9, 12]]


def test_regression_control_matches_hand_calculation():
    new = [P(batch="N1", time_months=6, value=98.8), P(batch="N1", time_months=9, value=96.0)]
    r = detect_oot(HIST, new, "regression_control", alpha=0.01)
    t = np.array([p.time_months for p in HIST]); y = np.array([p.value for p in HIST])
    b1, b0 = np.polyfit(t, y, 1); n = len(y)
    s = np.sqrt(((y - (b0 + b1 * t)) ** 2).sum() / (n - 2))
    half = stats.t.ppf(0.995, n - 2) * s * np.sqrt(1 + 1 / n + (9 - t.mean()) ** 2 / ((t - t.mean()) ** 2).sum())
    c = r.checks[1]
    assert c.upper - c.predicted == pytest.approx(half)
    assert [c.oot for c in r.checks] == [False, True]


def test_by_time_point():
    r = detect_oot(HIST, [P(batch="N", time_months=12, value=97.5), P(batch="N", time_months=24, value=95)], "by_time_point")
    assert r.checks[0].oot is False
    assert r.checks[1].oot is None and "historical" in r.checks[1].note


def test_slope_control():
    hist = [P(batch=b, time_months=t, value=100 + s * t) for b, s in [("H1", -0.20), ("H2", -0.22), ("H3", -0.19), ("H4", -0.21)]
            for t in [0, 3, 6, 9]]
    new = [P(batch="N", time_months=t, value=100 - 0.6 * t) for t in [0, 3, 6]]
    assert detect_oot(hist, new, "slope_control").checks[0].oot is True


def test_conditions_general_and_variants():
    r = sc("drug_product")
    assert [c.condition for c in r.conditions] == ["25°C ± 2°C/60% RH ± 5% RH", "30°C ± 2°C/65% RH ± 5% RH",
                                                    "40°C ± 2°C/75% RH ± 5% RH"]
    assert [c.study for c in sc("drug_substance", long_term="30C/65RH").conditions] == ["long_term", "accelerated"]
    semi = sc("drug_product", "semi_permeable_container")
    assert semi.conditions[-1].condition == "40°C ± 2°C/NMT 25% RH"
    assert sc("drug_product", "refrigerator").conditions[0].condition == "5°C ± 3°C"
    assert sc("drug_substance", "freezer").conditions[0].condition == "-20°C ± 5°C"
    assert len(sc("drug_product").significant_change_definition) == 5
    assert sc("drug_product", climatic_zone="IVb").who_zone_long_term.startswith("30°C ± 2°C/75%")
