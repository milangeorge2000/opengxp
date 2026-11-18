"""ICH Q1E Appendix B: regression-based shelf life with ANCOVA batch poolability.

* Poolability (B.2.2.1): test equal slopes first, then equal intercepts, each at alpha = 0.25.
  Model chosen: separate slopes+intercepts / common slope with separate intercepts / fully pooled.
* Shelf life (2.6, B.1): earliest time the 95% confidence limit for the mean crosses the acceptance
  criterion; one-sided for a known direction, two-sided for 'both'. Separate-slope estimates use the
  pooled mean square error from all batches (B.2.1). The shortest batch estimate applies to all batches.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from scipy import optimize, stats

from opengxp.core import Citation, Finding, GxPInputError, GxPResult, provenance
from opengxp.stability.extrapolation import AccelChange, ExtrapolationResult, Storage, extrapolation_limit
from opengxp.stability.models import Attribute, StabilityPoint

Q1E_URL = "https://database.ich.org/sites/default/files/Q1E%20Guideline.pdf"
ModelName = Literal["separate", "common_slope", "pooled"]


class _Fit:
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X, self.y = X, y
        self.beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        self.resid = y - X @ self.beta
        self.sse = float(self.resid @ self.resid)
        self.p = np.linalg.matrix_rank(X)
        self.df = len(y) - self.p
        self.mse = self.sse / self.df if self.df > 0 else float("nan")
        self.xtx_inv = np.linalg.pinv(X.T @ X)


def _design(model: ModelName, b_idx: np.ndarray, t: np.ndarray, k: int) -> np.ndarray:
    onehot = np.eye(k)[b_idx]
    if model == "separate":
        return np.hstack([onehot, onehot * t[:, None]])
    if model == "common_slope":
        return np.hstack([onehot, t[:, None]])
    return np.column_stack([np.ones_like(t), t])


def _row(model: ModelName, b: int, t: np.ndarray, k: int) -> np.ndarray:
    return _design(model, np.full(len(t), b), t, k)


def _f_test(reduced: _Fit, full: _Fit) -> float:
    ddf = reduced.df - full.df
    if ddf <= 0 or full.df <= 0 or full.sse <= 0:
        return 1.0 if reduced.sse - full.sse <= 1e-12 else 0.0
    f = ((reduced.sse - full.sse) / ddf) / (full.sse / full.df)
    return float(stats.f.sf(f, ddf, full.df))


class BatchEstimate(BaseModel):
    batch: str
    intercept: float
    slope_per_month: float
    crossing_months: float | None = Field(description="Where the 95% bound meets the criterion; null = not within the search horizon.")
    limiting_spec: Literal["lower", "upper"] | None = None


class Poolability(BaseModel):
    slopes_p_value: float | None
    intercepts_p_value: float | None
    alpha: float
    model: ModelName
    explanation: str


class CurvePoint(BaseModel):
    time_months: float
    batch: str
    fitted: float
    lower_bound: float | None
    upper_bound: float | None


class ShelfLifeResult(GxPResult):
    attribute: str
    n_batches: int
    n_points: int
    long_term_months: float = Field(description="X: longest time point in the long-term data.")
    poolability: Poolability
    confidence: str
    residual_df: int
    residual_sd: float
    batches: list[BatchEstimate]
    statistical_shelf_life_months: float | None = Field(description="Shortest batch crossing (unrounded).")
    supported_shelf_life_months: int | None = Field(description="Whole months supported: statistics capped by extrapolation rules where assessed.")
    extrapolation: ExtrapolationResult | None = None
    proposed_shelf_life_months: float | None = None
    proposed_supported: bool | None = None
    curve: list[CurvePoint] | None = None


def estimate_shelf_life(
    attribute: Attribute,
    points: list[StabilityPoint],
    proposed_shelf_life_months: float | None = None,
    pooling_alpha: float = 0.25,
    confidence: float = 0.95,
    storage: Storage | None = None,
    accelerated_significant_change: AccelChange | None = None,
    intermediate_significant_change: bool | None = None,
    horizon_months: float | None = None,
    return_curve: bool = False,
) -> ShelfLifeResult:
    a = attribute
    need = {"decreasing": ["lower_spec"], "increasing": ["upper_spec"], "both": ["lower_spec", "upper_spec"]}[a.direction]
    missing = [s for s in need if getattr(a, s) is None]
    if missing:
        raise GxPInputError(f"direction='{a.direction}' needs {', '.join(missing)}.")
    if len(points) < 3:
        raise GxPInputError("Need at least 3 data points for a regression.")
    batches = sorted({p.batch for p in points})
    k = len(batches)
    bi = {b: i for i, b in enumerate(batches)}
    t = np.array([p.time_months for p in points], float)
    y = np.array([p.value for p in points], float)
    lo, hi = a.lower_spec, a.upper_spec
    if a.transform == "log":
        if (y <= 0).any() or (lo is not None and lo <= 0) or (hi is not None and hi <= 0):
            raise GxPInputError("transform='log' needs strictly positive values and specs.")
        y = np.log(y)
        lo = math.log(lo) if lo is not None else None
        hi = math.log(hi) if hi is not None else None
    b_idx = np.array([bi[p.batch] for p in points])

    findings: list[Finding] = []
    if k < 3:
        findings.append(Finding(level="warning", code="FEW_BATCHES",
                                message=f"{k} batch(es); Q1A/Q1E expect at least 3 primary batches."))
    per_batch_times = [len(set(t[b_idx == i])) for i in range(k)]
    if min(per_batch_times) < 3:
        findings.append(Finding(level="warning", code="FEW_TIME_POINTS",
                                message="At least one batch has fewer than 3 distinct time points."))

    # ---- poolability (B.2.2.1) -------------------------------------------------------------------
    fits = {m: _Fit(_design(m, b_idx, t, k), y) for m in ("separate", "common_slope", "pooled")}
    p_slope = p_int = None
    if k == 1:
        model: ModelName = "pooled"
        why = "Single batch: one regression line."
    else:
        p_slope = _f_test(fits["common_slope"], fits["separate"])
        if p_slope < pooling_alpha:
            model, why = "separate", (f"Slopes differ (p = {p_slope:.3f} < {pooling_alpha}): individual slopes and "
                                      "intercepts with the pooled MSE; shortest batch estimate applies.")
        else:
            p_int = _f_test(fits["pooled"], fits["common_slope"])
            if p_int < pooling_alpha:
                model, why = "common_slope", (f"Slopes poolable (p = {p_slope:.3f}) but intercepts differ "
                                              f"(p = {p_int:.3f} < {pooling_alpha}): common slope, individual intercepts.")
            else:
                model, why = "pooled", (f"Slopes (p = {p_slope:.3f}) and intercepts (p = {p_int:.3f}) poolable at "
                                        f"alpha = {pooling_alpha}: all batches combined.")
    fit = fits[model]
    if fit.df <= 0:
        raise GxPInputError("Not enough data for the selected model (no residual degrees of freedom). Add time points.")
    cov = fit.mse * fit.xtx_inv
    two_sided = a.direction == "both"
    q = float(stats.t.ppf(1 - (1 - confidence) / (2 if two_sided else 1), fit.df))

    X_months = float(t.max())
    horizon = horizon_months or max(60.0, 5 * X_months)

    def bounds(b: int, tt: np.ndarray):
        R = _row(model, b, tt, k)
        mean = R @ fit.beta
        se = np.sqrt(np.einsum("ij,jk,ik->i", R, cov, R))
        return mean, mean - q * se, mean + q * se

    def margin(b: int, tt: np.ndarray) -> np.ndarray:
        _, lb, ub = bounds(b, tt)
        m = np.full(len(tt), np.inf)
        if lo is not None and a.direction in ("decreasing", "both"):
            m = np.minimum(m, lb - lo)
        if hi is not None and a.direction in ("increasing", "both"):
            m = np.minimum(m, hi - ub)
        return m

    est_batches = [0] if model == "pooled" else range(k)
    out: list[BatchEstimate] = []
    grid = np.linspace(0, horizon, int(horizon * 20) + 1)
    for b in est_batches:
        mg = margin(b, grid)
        idx = np.argmax(mg < 0) if (mg < 0).any() else None
        cross = None
        limiting = None
        if idx is not None:
            if idx == 0:
                cross = 0.0
            else:
                cross = float(optimize.brentq(lambda s: margin(b, np.array([s]))[0], grid[idx - 1], grid[idx]))
            _, lb, ub = bounds(b, np.array([cross + 0.01]))
            m_lo = lb[0] - lo if lo is not None and a.direction != "increasing" else np.inf
            m_hi = hi - ub[0] if hi is not None and a.direction != "decreasing" else np.inf
            limiting = "lower" if m_lo <= m_hi else "upper"
        R0 = _row(model, b, np.array([0.0, 1.0]), k) @ fit.beta
        out.append(BatchEstimate(batch="all (pooled)" if model == "pooled" else batches[b],
                                 intercept=float(R0[0]), slope_per_month=float(R0[1] - R0[0]),
                                 crossing_months=None if cross is None else round(cross, 2), limiting_spec=limiting))

    crossings = [e.crossing_months for e in out if e.crossing_months is not None]
    stat_sl = min(crossings) if crossings else None
    if stat_sl is None:
        findings.append(Finding(level="info", code="NO_CROSSING_IN_HORIZON",
                                message=f"The 95% bound does not reach the criterion within {horizon:g} months."))
    supported: int | None = math.floor(stat_sl) if stat_sl is not None else None

    # ---- model checks --------------------------------------------------------------------------
    if fit.df >= 2 and len(set(t)) >= 3:
        quad = _Fit(np.hstack([fit.X, (t ** 2)[:, None]]), y)
        if quad.df > 0 and _f_test(fit, quad) < 0.05:
            findings.append(Finding(level="warning", code="CURVATURE",
                                    message="A quadratic time term is significant (p < 0.05): the linear model may not fit; "
                                            "consider transform='log' or a non-linear model (Q1E 2.6)."))
    if fit.mse > 0:
        h = np.einsum("ij,jk,ik->i", fit.X, fit.xtx_inv, fit.X)
        r = fit.resid / np.sqrt(fit.mse * np.clip(1 - h, 1e-12, None))
        for i in np.where(np.abs(r) > 3)[0]:
            p = points[i]
            findings.append(Finding(level="warning", code="OUTLIER",
                                    message=f"Batch {p.batch} at {p.time_months:g} months (value {p.value:g}) has a "
                                            f"studentised residual of {r[i]:.1f}: check for an analytical or OOT issue."))

    # ---- extrapolation (Q1E 2.4/2.5) -----------------------------------------------------------------
    extra = None
    if storage is not None and accelerated_significant_change is not None:
        extra = extrapolation_limit(storage, X_months, accelerated_significant_change, intermediate_significant_change,
                                    little_change_and_variability=False, amenable_to_statistics=True,
                                    statistical_analysis_performed=True, supporting_data=True)
        cap = math.floor(extra.max_shelf_life_months)
        supported = cap if supported is None else min(supported, cap)
    elif stat_sl is None or stat_sl > X_months:
        findings.append(Finding(level="warning", code="EXTRAPOLATION_NOT_ASSESSED",
                                message=f"The statistical estimate goes beyond the {X_months:g} months of long-term data. "
                                        "Give storage and accelerated_significant_change to apply the Q1E extrapolation limits."))

    prop_ok = None
    if proposed_shelf_life_months is not None:
        prop_ok = supported is not None and proposed_shelf_life_months <= supported
        findings.append(Finding(level="info" if prop_ok else "action_required",
                                code="PROPOSAL_SUPPORTED" if prop_ok else "PROPOSAL_NOT_SUPPORTED",
                                message=f"Proposed {proposed_shelf_life_months:g} months is "
                                        f"{'supported' if prop_ok else 'not supported'} (supported: {supported} months)."))

    curve = None
    if return_curve:
        tt = np.arange(0, math.ceil(max(supported or 0, X_months, stat_sl or 0)) + 1, 1.0)
        inv = np.exp if a.transform == "log" else (lambda v: v)
        curve = []
        for b in est_batches:
            m, lb, ub = bounds(b, tt)
            for j, tv in enumerate(tt):
                curve.append(CurvePoint(time_months=float(tv), batch=out[list(est_batches).index(b)].batch,
                                        fitted=float(inv(m[j])),
                                        lower_bound=float(inv(lb[j])) if a.direction != "increasing" else None,
                                        upper_bound=float(inv(ub[j])) if a.direction != "decreasing" else None))

    side = "two-sided" if two_sided else "one-sided"
    summary = (f"{a.name}: {why} {int(confidence * 100)}% {side} confidence bound "
               + (f"meets the acceptance criterion at {stat_sl:g} months; " if stat_sl is not None else "does not meet the criterion; ")
               + (f"supported shelf life {supported} months" if supported is not None else "supported shelf life not determined")
               + (f" (extrapolation limit {extra.max_shelf_life_months:g} months, {extra.section})." if extra else "."))
    return ShelfLifeResult(
        summary=summary,
        findings=findings,
        assumptions=[
            "Linear model on the " + ("log scale" if a.transform == "log" else "original scale") + "; time is the only covariate.",
            "Each record is one result; replicates are treated as independent observations.",
            "Supported shelf life is the statistical estimate floored to whole months.",
        ],
        citations=[
            Citation(guideline="ICH Q1E", section="2.6 and Appendix B.1",
                     rule="Shelf life = earliest time the 95% confidence limit for the mean meets the acceptance criterion.", url=Q1E_URL),
            Citation(guideline="ICH Q1E", section="Appendix B.2 (B.2.1, B.2.2.1)",
                     rule="ANCOVA: slopes then intercepts at alpha 0.25; shortest batch estimate if not poolable.", url=Q1E_URL),
        ] + ([Citation(guideline="ICH Q1E", section=extra.section, rule="Extrapolation limit.", url=Q1E_URL)] if extra else []),
        provenance=provenance("stab_estimate_shelf_life", "Q1E", dict(
            attribute=a.model_dump(), points=[p.model_dump() for p in points], proposed=proposed_shelf_life_months,
            alpha=pooling_alpha, conf=confidence, storage=storage, acc=accelerated_significant_change,
            inter=intermediate_significant_change, horizon=horizon_months),
            ["statsmodels OLS/ANCOVA cross-check", "analytic single-batch cases", "Q1E 2.4/2.5 extrapolation rules"]),
        attribute=a.name, n_batches=k, n_points=len(points), long_term_months=X_months,
        poolability=Poolability(slopes_p_value=p_slope, intercepts_p_value=p_int, alpha=pooling_alpha, model=model, explanation=why),
        confidence=f"{confidence:.0%} {side}", residual_df=fit.df, residual_sd=math.sqrt(fit.mse) if fit.mse == fit.mse else 0.0,
        batches=out, statistical_shelf_life_months=stat_sl, supported_shelf_life_months=supported,
        extrapolation=extra, proposed_shelf_life_months=proposed_shelf_life_months, proposed_supported=prop_ok, curve=curve,
    )
