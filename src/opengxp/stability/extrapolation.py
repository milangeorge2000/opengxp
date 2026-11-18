"""ICH Q1E sections 2.4-2.5 / Appendix A: how far a retest period or shelf life may extend beyond
the period covered by long-term data (X)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Citation, GxPInputError, GxPResult, provenance

Storage = Literal["room_temperature", "refrigerated", "freezer", "below_minus_20C"]
AccelChange = Literal["none", "within_3_months", "between_3_and_6_months"]


class ExtrapolationResult(BaseModel):
    long_term_months: float = Field(description="X: period covered by long-term data.")
    max_shelf_life_months: float = Field(description="Y: the longest retest period / shelf life Q1E allows here.")
    extrapolation_allowed: bool
    rule_path: list[str] = Field(description="The Q1E decision path taken, step by step.")
    section: str
    conditions: list[str] = Field(default_factory=list, description="What the proposal must be backed by.")


def _cap(x: float, factor: float, plus: float) -> float:
    return min(factor * x, x + plus)


def extrapolation_limit(
    storage: Storage,
    long_term_months: float,
    accelerated_significant_change: AccelChange = "none",
    intermediate_significant_change: bool | None = None,
    little_change_and_variability: bool = False,
    amenable_to_statistics: bool = True,
    statistical_analysis_performed: bool = True,
    supporting_data: bool = True,
) -> ExtrapolationResult:
    X = long_term_months
    if X <= 0:
        raise GxPInputError("long_term_months must be > 0 (the period covered by long-term data).")
    path: list[str] = [f"X = {X:g} months of long-term data", f"Storage: {storage}"]
    cond: list[str] = []
    stats_ok = amenable_to_statistics and statistical_analysis_performed

    def res(y: float, section: str) -> ExtrapolationResult:
        return ExtrapolationResult(long_term_months=X, max_shelf_life_months=round(y, 2),
                                   extrapolation_allowed=y > X, rule_path=path, section=section, conditions=cond)

    if storage == "freezer":
        path.append("Freezer storage: shelf life based on long-term data; no extrapolation")
        cond.append("Test one batch at an elevated temperature to cover short excursions (shipping/handling).")
        return res(X, "Q1E 2.5.2")
    if storage == "below_minus_20C":
        path.append("Storage below -20°C: based on long-term data, case by case")
        return res(X, "Q1E 2.5.3")

    if storage == "refrigerated":
        if accelerated_significant_change != "none":
            path.append(f"Significant change at accelerated condition ({accelerated_significant_change.replace('_', ' ')})")
            path.append("No extrapolation; a shorter shelf life can be called for")
            cond.append("Statistical analysis if long-term data show variability.")
            if accelerated_significant_change == "within_3_months":
                cond.append("Discuss the effect of short-term excursions outside the label condition.")
            return res(X, "Q1E 2.5.1.2")
        path.append("No significant change at accelerated condition")
        if little_change_and_variability:
            path.append("Long-term and accelerated data: little change and little variability")
            path.append("Y = up to 1.5X, not more than X + 6 months")
            return res(_cap(X, 1.5, 6), "Q1E 2.5.1.1")
        path.append("Long-term or accelerated data show change and/or variability")
        if stats_ok and supporting_data:
            path.append("Data amenable to statistics and analysis performed, backed by supporting data")
            path.append("Y = up to 1.5X, not more than X + 6 months")
            cond.append("Backed by the statistical analysis and relevant supporting data.")
            return res(_cap(X, 1.5, 6), "Q1E 2.5.1.1")
        if supporting_data:
            path.append("No statistical analysis (or data not amenable), relevant supporting data provided")
            path.append("Y = up to X + 3 months")
            return res(X + 3, "Q1E 2.5.1.1")
        path.append("No statistical analysis and no supporting data: no extrapolation")
        return res(X, "Q1E 2.5.1.1")

    # room temperature
    if accelerated_significant_change == "none":
        path.append("No significant change at accelerated condition")
        if little_change_and_variability:
            path.append("Long-term and accelerated data: little or no change and little or no variability")
            path.append("Statistical analysis normally unnecessary; Y = up to 2X, not more than X + 12 months")
            cond.append("Justify omitting statistical analysis (change pattern, mass balance, supporting data).")
            return res(_cap(X, 2, 12), "Q1E 2.4.1.1")
        path.append("Long-term or accelerated data show change and/or variability")
        if stats_ok and supporting_data:
            path.append("Data amenable to statistics and analysis performed")
            path.append("Y = up to 2X, not more than X + 12 months")
            cond.append("Backed by the statistical analysis and relevant supporting data.")
            return res(_cap(X, 2, 12), "Q1E 2.4.1.2")
        if supporting_data:
            path.append("No statistical analysis (or data not amenable), relevant supporting data provided")
            path.append("Y = up to 1.5X, not more than X + 6 months")
            return res(_cap(X, 1.5, 6), "Q1E 2.4.1.2")
        path.append("No statistical analysis and no supporting data: no extrapolation")
        return res(X, "Q1E 2.4.1.2")

    path.append("Significant change at accelerated condition")
    if intermediate_significant_change is None:
        raise GxPInputError(
            "Significant change at the accelerated condition requires the intermediate-condition result "
            "(Q1E 2.4.2, Q1A(R2) 2.2.7.1). Pass intermediate_significant_change=true/false.")
    if intermediate_significant_change:
        path.append("Significant change at intermediate condition")
        path.append("Y must not exceed X; a shorter shelf life can be called for")
        return res(X, "Q1E 2.4.2.2")
    path.append("No significant change at intermediate condition")
    if stats_ok and supporting_data:
        path.append("Data amenable to statistics and analysis performed")
        path.append("Y = up to 1.5X, not more than X + 6 months")
        cond.append("Backed by the statistical analysis and relevant supporting data.")
        return res(_cap(X, 1.5, 6), "Q1E 2.4.2.1")
    if supporting_data:
        path.append("No statistical analysis (or data not amenable), relevant supporting data provided")
        path.append("Y = up to X + 3 months")
        return res(X + 3, "Q1E 2.4.2.1")
    path.append("No statistical analysis and no supporting data: no extrapolation")
    return res(X, "Q1E 2.4.2.1")


class ExtrapolationReport(GxPResult, ExtrapolationResult):
    """ExtrapolationResult wrapped in the standard OpenGxP envelope (used by the MCP tool)."""


def extrapolation_report(**kw) -> ExtrapolationReport:
    r = extrapolation_limit(**kw)
    q1e = "https://database.ich.org/sites/default/files/Q1E%20Guideline.pdf"
    return ExtrapolationReport(
        **r.model_dump(),
        summary=(f"Y up to {r.max_shelf_life_months:g} months with X = {r.long_term_months:g} months "
                 f"({'extrapolation allowed' if r.extrapolation_allowed else 'no extrapolation'}; {r.section})."),
        assumptions=["Inputs describe each attribute's worst case; evaluate attributes separately (Q1E 2.4)."],
        citations=[Citation(guideline="ICH Q1E", section=r.section + " and Appendix A",
                            rule=r.rule_path[-1], url=q1e)],
        provenance=provenance("stab_extrapolation_limit", "Q1E", kw, ["every branch of Q1E 2.4-2.5"]),
    )
