"""Out-of-trend (OOT) detection for stability results.

Methods follow the PhRMA CMC Statistics and Stability Expert Teams paper "Identification of
Out-of-Trend Stability Results" (Pharmaceutical Technology, 2003; follow-up 2005). OOT is not
defined by ICH; this tool says so in its citations.

* regression_control: regression of all historical results on time; a new result is OOT if it is
  outside the prediction interval for a new observation at its time point.
* by_time_point: historical results at the same time point (±0.5 month); prediction interval for
  a new observation from their mean and SD.
* slope_control: the new batch's slope against the distribution of historical batch slopes.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from scipy import stats

from opengxp.core import Citation, Finding, GxPInputError, GxPResult, provenance
from opengxp.stability.models import StabilityPoint

Method = Literal["regression_control", "by_time_point", "slope_control"]
PHRMA = "PhRMA CMC Statistics and Stability Expert Teams, Identification of Out-of-Trend Stability Results, Pharmaceutical Technology (2003)"


class OOTCheck(BaseModel):
    batch: str
    time_months: float | None = Field(description="Null for slope_control (batch-level check).")
    observed: float = Field(description="Observed value (or the batch slope for slope_control).")
    predicted: float | None
    lower: float | None
    upper: float | None
    oot: bool | None = Field(description="Null when there is not enough history to judge.")
    note: str | None = None


class OOTResult(GxPResult):
    method: Method
    alpha: float
    n_historical: int
    checks: list[OOTCheck]


def _pi(mean: float, sd: float, n: int, alpha: float, extra: float = 0.0) -> tuple[float, float]:
    q = stats.t.ppf(1 - alpha / 2, n - 1)
    half = q * sd * np.sqrt(1 + 1 / n + extra)
    return mean - half, mean + half


def detect_oot(historical: list[StabilityPoint], new_points: list[StabilityPoint],
               method: Method = "regression_control", alpha: float = 0.01,
               attribute_name: str = "attribute") -> OOTResult:
    if not historical or not new_points:
        raise GxPInputError("Give both historical points (approved batches) and new_points to check.")
    th = np.array([p.time_months for p in historical])
    yh = np.array([p.value for p in historical])
    checks: list[OOTCheck] = []
    findings: list[Finding] = []

    if method == "regression_control":
        if len(th) < 3 or len(set(th)) < 2:
            raise GxPInputError("regression_control needs at least 3 historical points at 2 or more time points.")
        X = np.column_stack([np.ones_like(th), th])
        beta, *_ = np.linalg.lstsq(X, yh, rcond=None)
        resid = yh - X @ beta
        n = len(yh)
        s = float(np.sqrt(resid @ resid / (n - 2)))
        tbar, sxx = th.mean(), float(((th - th.mean()) ** 2).sum())
        q = stats.t.ppf(1 - alpha / 2, n - 2)
        for p in new_points:
            pred = float(beta[0] + beta[1] * p.time_months)
            half = q * s * np.sqrt(1 + 1 / n + (p.time_months - tbar) ** 2 / sxx)
            checks.append(OOTCheck(batch=p.batch, time_months=p.time_months, observed=p.value, predicted=pred,
                                   lower=pred - half, upper=pred + half, oot=not (pred - half <= p.value <= pred + half)))
    elif method == "by_time_point":
        for p in new_points:
            same = yh[np.abs(th - p.time_months) <= 0.5]
            if len(same) < 3:
                checks.append(OOTCheck(batch=p.batch, time_months=p.time_months, observed=p.value, predicted=None,
                                       lower=None, upper=None, oot=None,
                                       note=f"Only {len(same)} historical results at this time point (need 3)."))
                continue
            lo, hi = _pi(float(same.mean()), float(same.std(ddof=1)), len(same), alpha)
            checks.append(OOTCheck(batch=p.batch, time_months=p.time_months, observed=p.value,
                                   predicted=float(same.mean()), lower=lo, upper=hi, oot=not (lo <= p.value <= hi)))
    else:  # slope_control
        def slopes(pts: list[StabilityPoint]) -> dict[str, float]:
            out = {}
            for b in sorted({p.batch for p in pts}):
                tt = np.array([p.time_months for p in pts if p.batch == b])
                yy = np.array([p.value for p in pts if p.batch == b])
                if len(set(tt)) >= 3:
                    out[b] = float(np.polyfit(tt, yy, 1)[0])
            return out
        hist = slopes(historical)
        if len(hist) < 3:
            raise GxPInputError("slope_control needs at least 3 historical batches with 3+ time points each.")
        hs = np.array(list(hist.values()))
        lo, hi = _pi(float(hs.mean()), float(hs.std(ddof=1)), len(hs), alpha)
        new = slopes(new_points)
        for b in sorted({p.batch for p in new_points}):
            if b not in new:
                checks.append(OOTCheck(batch=b, time_months=None, observed=float("nan"), predicted=float(hs.mean()),
                                       lower=lo, upper=hi, oot=None, note="New batch needs 3+ time points for a slope."))
            else:
                checks.append(OOTCheck(batch=b, time_months=None, observed=new[b], predicted=float(hs.mean()),
                                       lower=lo, upper=hi, oot=not (lo <= new[b] <= hi)))

    for c in checks:
        if c.oot:
            where = f"at {c.time_months:g} months " if c.time_months is not None else "(slope) "
            findings.append(Finding(level="action_required", code="OOT",
                                    message=f"Batch {c.batch} {where}is out of trend: {c.observed:.4g} outside "
                                            f"[{c.lower:.4g}, {c.upper:.4g}]. Investigate per site OOT SOP."))
    n_oot = sum(bool(c.oot) for c in checks)
    return OOTResult(
        summary=f"{attribute_name}: {n_oot} of {len(checks)} results out of trend ({method}, "
                f"{(1 - alpha):.0%} prediction interval).",
        findings=findings,
        assumptions=["Historical batches are representative and themselves in trend.",
                     "OOT limits are statistical alert limits, not specifications."],
        citations=[Citation(guideline=PHRMA, section=method.replace("_", " "),
                            rule="Prediction-interval approach to OOT; not an ICH requirement.",
                            url=None),
                   Citation(guideline="FDA Guidance: Investigating OOS Test Results (2022)", section="scope",
                            rule="OOS investigations; OOT handling is set by site SOPs.", url=None)],
        provenance=provenance("stab_detect_oot", "PhRMA-OOT-2003", dict(
            h=[p.model_dump() for p in historical], n=[p.model_dump() for p in new_points], m=method, a=alpha),
            ["hand-computed prediction intervals"]),
        method=method, alpha=alpha, n_historical=len(historical), checks=checks,
    )
