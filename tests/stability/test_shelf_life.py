"""Q1E shelf life: independent cross-checks against statsmodels OLS/ANCOVA, analytic cases, and
the Q1E poolability decision logic."""

import math

import numpy as np
import pandas as pd
import pytest
import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm

from opengxp.core import GxPInputError
from opengxp.stability.models import Attribute, StabilityPoint as P, parse_csv
from opengxp.stability.shelf_life import estimate_shelf_life as run

ASSAY = Attribute(name="Assay", direction="decreasing", lower_spec=95.0)
TIMES = [0, 3, 6, 9, 12, 18]


def make(batches, noise=0.3, seed=1, times=TIMES):
    rng = np.random.default_rng(seed)
    return [P(batch=b, time_months=t, value=a0 + s * t + rng.normal(0, noise)) for b, a0, s in batches for t in times]


def df(points):
    return pd.DataFrame([p.model_dump() for p in points])


def test_ancova_p_values_match_statsmodels():
    pts = make([("A", 100, -0.25), ("B", 100.4, -0.30), ("C", 99.7, -0.22)])
    r = run(ASSAY, pts)
    d = df(pts)
    full = smf.ols("value ~ C(batch) * time_months", d).fit()
    cs = smf.ols("value ~ C(batch) + time_months", d).fit()
    pooled = smf.ols("value ~ time_months", d).fit()
    p_slope = anova_lm(cs, full)["Pr(>F)"].iloc[1]
    p_int = anova_lm(pooled, cs)["Pr(>F)"].iloc[1]
    assert r.poolability.slopes_p_value == pytest.approx(p_slope, rel=1e-9)
    if r.poolability.intercepts_p_value is not None:
        assert r.poolability.intercepts_p_value == pytest.approx(p_int, rel=1e-9)


def test_pooled_crossing_matches_statsmodels_confidence_bound():
    pts = make([("A", 100, -0.25), ("B", 100, -0.25), ("C", 100, -0.25)], noise=0.4, seed=3)
    r = run(ASSAY, pts)
    assert r.poolability.model == "pooled"
    fit = smf.ols("value ~ time_months", df(pts)).fit()
    t_cross = r.statistical_shelf_life_months
    pred = fit.get_prediction(pd.DataFrame({"time_months": [t_cross]})).summary_frame(alpha=0.10)  # two-sided 90% = one-sided 95%
    assert pred["mean_ci_lower"].iloc[0] == pytest.approx(95.0, abs=0.01)
    assert r.batches[0].slope_per_month == pytest.approx(fit.params["time_months"], rel=1e-9)
    assert r.residual_sd == pytest.approx(math.sqrt(fit.mse_resid), rel=1e-9)


def test_separate_slopes_use_pooled_mse_and_shortest_batch():
    pts = make([("A", 100, -0.10), ("B", 100, -0.40), ("C", 100, -0.25)], noise=0.2)
    r = run(ASSAY, pts)
    assert r.poolability.model == "separate"
    fit = smf.ols("value ~ C(batch) * time_months - 1 - time_months", df(pts)).fit()  # per-batch intercept & slope
    assert r.residual_df == fit.df_resid
    worst = min(r.batches, key=lambda b: b.crossing_months)
    assert worst.batch == "B"
    assert r.statistical_shelf_life_months == worst.crossing_months


def test_common_slope_when_only_intercepts_differ():
    r = run(ASSAY, make([("A", 100, -0.25), ("B", 101.5, -0.25), ("C", 98.8, -0.25)], noise=0.15))
    assert r.poolability.model == "common_slope"
    assert len({round(b.slope_per_month, 9) for b in r.batches}) == 1


def test_near_noise_free_line_crosses_at_analytic_time():
    pts = make([("A", 100, -0.25)], noise=1e-6, times=[0, 3, 6, 9, 12, 18, 24])
    r = run(ASSAY, pts)
    assert r.statistical_shelf_life_months == pytest.approx(20.0, abs=0.01)


def test_increasing_degradant_upper_spec():
    deg = Attribute(name="Imp B", direction="increasing", upper_spec=1.0)
    pts = make([("A", 0.10, 0.03), ("B", 0.12, 0.03), ("C", 0.09, 0.03)], noise=1e-6)
    r = run(deg, pts)
    assert r.batches[0].limiting_spec == "upper"
    assert r.statistical_shelf_life_months == pytest.approx(min((1.0 - a) / 0.03 for a in (0.10, 0.12, 0.09)), abs=0.05)


def test_two_sided_is_more_conservative_than_one_sided():
    pts = make([("A", 100, -0.25), ("B", 100, -0.25), ("C", 100, -0.25)], noise=0.5, seed=7)
    one = run(ASSAY, pts).statistical_shelf_life_months
    two = run(Attribute(name="Assay", direction="both", lower_spec=95, upper_spec=105), pts).statistical_shelf_life_months
    assert two < one


def test_log_transform():
    pts = [P(batch="A", time_months=t, value=100 * math.exp(-0.01 * t) * (1 + 1e-7 * i)) for i, t in enumerate([0, 3, 6, 9, 12])]
    r = run(Attribute(name="Assay", direction="decreasing", lower_spec=90, transform="log"), pts)
    assert r.statistical_shelf_life_months == pytest.approx(-math.log(0.9) / 0.01, abs=0.05)


def test_extrapolation_caps_supported_shelf_life():
    pts = make([("A", 100, -0.05), ("B", 100, -0.05), ("C", 100, -0.05)], noise=0.1, times=[0, 3, 6, 9, 12])
    r = run(ASSAY, pts, storage="room_temperature", accelerated_significant_change="none", proposed_shelf_life_months=36)
    assert r.statistical_shelf_life_months is None or r.statistical_shelf_life_months > 24
    assert r.supported_shelf_life_months == 24          # 2X = 24 <= X + 12 = 24
    assert r.proposed_supported is False
    r2 = run(ASSAY, pts)
    assert any(f.code == "EXTRAPOLATION_NOT_ASSESSED" for f in r2.findings)


def test_outlier_flagged():
    pts = make([("A", 100, -0.25), ("B", 100, -0.25), ("C", 100, -0.25)], noise=0.1)
    pts[8] = P(batch=pts[8].batch, time_months=pts[8].time_months, value=pts[8].value - 3)
    assert any(f.code == "OUTLIER" for f in run(ASSAY, pts).findings)


def test_curve_returned():
    r = run(ASSAY, make([("A", 100, -0.25)]), return_curve=True)
    assert r.curve and r.curve[0].time_months == 0 and r.curve[0].lower_bound is not None


def test_input_errors():
    with pytest.raises(GxPInputError, match="lower_spec"):
        run(Attribute(name="x", direction="decreasing"), make([("A", 100, -0.2)]))
    with pytest.raises(GxPInputError):
        run(ASSAY, make([("A", 100, -0.2)])[:2])


def test_parse_csv_aliases():
    pts = parse_csv("Lot,Time,Result\nA,0,100.1\nA,3,99.4\n")
    assert pts[1].batch == "A" and pts[1].time_months == 3 and pts[1].value == 99.4
    with pytest.raises(GxPInputError, match="line 2"):
        parse_csv("batch,time_months,value\nA,zero,100\n")
