"""MCP server: stability (ICH Q1E shelf life and extrapolation, Q1A(R2) storage conditions, OOT)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from opengxp.core import READ_ONLY_OFFLINE, GxPInputError
from opengxp.stability.conditions import SIGNIFICANT_CHANGE_PRODUCT, WHO_ZONES, StorageConditionResult, storage_conditions
from opengxp.stability.extrapolation import ExtrapolationReport, extrapolation_report
from opengxp.stability.models import Attribute, StabilityPoint, parse_csv
from opengxp.stability.oot import OOTResult, detect_oot
from opengxp.stability.shelf_life import ShelfLifeResult, estimate_shelf_life

stab = FastMCP(
    "opengxp-stability",
    instructions=(
        "ICH stability evaluation. stab_estimate_shelf_life runs the Q1E regression with ANCOVA batch poolability "
        "and applies the Q1E extrapolation limits; stab_extrapolation_limit answers 'how far beyond my long-term "
        "data may I go?'; stab_detect_oot flags out-of-trend results; stab_storage_conditions gives Q1A(R2) study "
        "conditions. Pass data inline as points or as CSV text (batch,time_months,value)."
    ),
    mask_error_details=True,
)

_POINTS = Annotated[list[StabilityPoint] | None, Field(max_length=2000, description="Long-term results, one per row.")]
_CSV = Annotated[str | None, Field(description="Alternative to points: CSV with header batch,time_months,value.")]


def _points(points: list[StabilityPoint] | None, csv_text: str | None, what: str = "points") -> list[StabilityPoint]:
    if points:
        return points
    if csv_text:
        return parse_csv(csv_text)
    raise GxPInputError(f"Give {what} or the matching csv_text (header batch,time_months,value).")


@stab.tool(title="Estimate shelf life (ICH Q1E)", tags={"stability", "ich-q1e", "validated"},
           annotations=READ_ONLY_OFFLINE, version="1.0", timeout=60,
           meta={"guidelines": ["ICH Q1E"], "validated_against": "statsmodels OLS/ANCOVA; Q1E 2.4/2.5 decision rules"})
def stab_estimate_shelf_life(
    attribute: Attribute,
    points: _POINTS = None,
    csv_text: _CSV = None,
    proposed_shelf_life_months: Annotated[float | None, Field(gt=0, description="Checked against what the data support.")] = None,
    storage: Annotated[Literal["room_temperature", "refrigerated", "freezer", "below_minus_20C"] | None,
                       Field(description="Intended storage; with accelerated_significant_change, applies Q1E extrapolation limits.")] = None,
    accelerated_significant_change: Annotated[Literal["none", "within_3_months", "between_3_and_6_months"] | None,
                                              Field(description="Q1A(R2) 'significant change' at the accelerated condition.")] = None,
    intermediate_significant_change: Annotated[bool | None, Field(description="Needed at room temperature when accelerated showed significant change.")] = None,
    pooling_alpha: Annotated[float, Field(gt=0, lt=1, description="Poolability significance level (Q1E: 0.25).")] = 0.25,
    return_curve: Annotated[bool, Field(description="Return fitted line and 95% bound per month, for plotting.")] = False,
) -> ShelfLifeResult:
    """Shelf life / retest period from long-term stability data, the Q1E way.

    Tests batch poolability (ANCOVA: slopes, then intercepts, alpha 0.25), picks the most reduced model, finds
    the earliest time the 95% confidence bound for the mean meets the acceptance criterion (one-sided for a known
    direction, two-sided for 'both'), takes the shortest batch, and caps the result with the Q1E extrapolation
    rules when storage and accelerated results are given. Flags curvature, outliers, few batches/time points.
    """
    return estimate_shelf_life(attribute, _points(points, csv_text), proposed_shelf_life_months, pooling_alpha,
                               storage=storage, accelerated_significant_change=accelerated_significant_change,
                               intermediate_significant_change=intermediate_significant_change, return_curve=return_curve)


@stab.tool(title="Extrapolation limit (ICH Q1E decision tree)", tags={"stability", "ich-q1e", "validated"},
           annotations=READ_ONLY_OFFLINE, version="1.0", timeout=10,
           meta={"guidelines": ["ICH Q1E"], "validated_against": "every branch of Q1E 2.4-2.5"})
def stab_extrapolation_limit(
    storage: Literal["room_temperature", "refrigerated", "freezer", "below_minus_20C"],
    long_term_months: Annotated[float, Field(gt=0, description="X: months covered by long-term data.")],
    accelerated_significant_change: Literal["none", "within_3_months", "between_3_and_6_months"] = "none",
    intermediate_significant_change: bool | None = None,
    little_change_and_variability: Annotated[bool, Field(description="Long-term AND accelerated data show little or no change and little or no variability.")] = False,
    amenable_to_statistics: bool = True,
    statistical_analysis_performed: bool = True,
    supporting_data: Annotated[bool, Field(description="Relevant supporting data (e.g. development batches) are available.")] = True,
) -> ExtrapolationReport:
    """Maximum retest period / shelf life (Y) that ICH Q1E allows beyond the long-term data (X), with the exact
    decision path (e.g. 'Y = up to 2X, not more than X + 12 months') and the section it comes from."""
    return extrapolation_report(
        storage=storage, long_term_months=long_term_months, accelerated_significant_change=accelerated_significant_change,
        intermediate_significant_change=intermediate_significant_change,
        little_change_and_variability=little_change_and_variability, amenable_to_statistics=amenable_to_statistics,
        statistical_analysis_performed=statistical_analysis_performed, supporting_data=supporting_data)


@stab.tool(title="Out-of-trend check", tags={"stability", "oot"},
           annotations=READ_ONLY_OFFLINE, version="1.0", timeout=30,
           meta={"method_source": "PhRMA OOT paper (2003)", "ich": False})
def stab_detect_oot(
    new_points: Annotated[list[StabilityPoint], Field(min_length=1, description="Results to check (new batch / latest pull).")],
    historical: _POINTS = None,
    historical_csv: _CSV = None,
    method: Annotated[Literal["regression_control", "by_time_point", "slope_control"],
                      Field(description="regression_control: prediction interval around the historical regression; "
                                        "by_time_point: historical results at the same time point; slope_control: new batch slope vs historical slopes.")] = "regression_control",
    alpha: Annotated[float, Field(gt=0, lt=0.5, description="Two-sided alert level (0.01 = 99% prediction interval).")] = 0.01,
    attribute_name: str = "attribute",
) -> OOTResult:
    """Flag stability results that are within specification but out of trend versus historical batches."""
    return detect_oot(_points(historical, historical_csv, "historical"), new_points, method, alpha, attribute_name)


@stab.tool(title="Stability storage conditions (ICH Q1A(R2))", tags={"stability", "ich-q1a", "validated"},
           annotations=READ_ONLY_OFFLINE, version="1.0", timeout=10, meta={"guidelines": ["ICH Q1A(R2)", "WHO TRS 1010 Annex 10"]})
def stab_storage_conditions(
    material: Literal["drug_substance", "drug_product"],
    case: Literal["general", "impermeable_container", "semi_permeable_container", "refrigerator", "freezer", "below_minus_20C"] = "general",
    long_term: Annotated[Literal["25C/60RH", "30C/65RH", "25C/40RH", "30C/35RH"] | None,
                         Field(description="Chosen long-term condition; 30C/65RH (or 30C/35RH) removes the intermediate study.")] = None,
    climatic_zone: Annotated[Literal["I", "II", "III", "IVa", "IVb"] | None, Field(description="WHO climatic zone of intended markets.")] = None,
) -> StorageConditionResult:
    """Long-term / intermediate / accelerated conditions and minimum months at submission, the 'significant
    change' definition, and case-specific notes (semi-permeable water loss, refrigerated excursions)."""
    return storage_conditions(material, case, long_term, climatic_zone)


@stab.resource("ich://q1a/significant-change", mime_type="application/json", tags={"ich-q1a"},
               description="ICH Q1A(R2) definition of 'significant change' for drug substance and drug product.")
def significant_change() -> dict:
    return {"drug_substance": ["Failure to meet its specification"], "drug_product": SIGNIFICANT_CHANGE_PRODUCT}


@stab.resource("who://climatic-zones", mime_type="application/json", tags={"stability"},
               description="WHO climatic zones and long-term testing conditions.")
def climatic_zones() -> dict:
    return WHO_ZONES


@stab.prompt(description="Draft a CTD 3.2.P.8.1 stability summary and shelf-life justification.", tags={"stability"})
def shelf_life_justification(product_name: str, attribute_names: str) -> str:
    return (
        f"Prepare the stability summary and shelf-life justification for {product_name}.\n"
        f"For each attribute ({attribute_names}): call stab_estimate_shelf_life with storage and the accelerated/"
        "intermediate significant-change results, return_curve=true. Report the poolability outcome with p-values, "
        "the model used, the crossing time per batch, the extrapolation rule path, and the supported shelf life. "
        "The product shelf life is the shortest across attributes. Quote numbers exactly, cite each section, list "
        "all warnings, and end with the disclaimer."
    )
