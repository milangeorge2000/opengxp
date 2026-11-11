"""ICH Q3A(R2) (drug substance) and Q3B(R2) (drug product) impurity thresholds.

Encodes Attachment 1 (threshold tables) and Attachment 2 (rounding and comparison convention)
of both guidelines:
  * results < 1.0% are reported to 2 decimals (Q3A) or to the reporting threshold's decimals (Q3B);
    results >= 1.0% to 1 decimal; half-up rounding;
  * an impurity is reported only if its rounded result is greater than (>) the reporting threshold;
  * a % threshold is compared with the reported result rounded to the threshold's decimals;
  * a TDI threshold is compared with the TDI computed from the reported result, rounded to the
    threshold's decimals; "exceeded" means strictly greater than.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Citation, Finding, GxPInputError, GxPResult, provenance
from opengxp.core.numbers import D, decimals_of, round_half_up

Q3A_URL = "https://database.ich.org/sites/default/files/Q3A%28R2%29%20Guideline.pdf"
Q3B_URL = "https://database.ich.org/sites/default/files/Q3B%28R2%29%20Guideline.pdf"

ProductType = Literal["drug_substance", "drug_product"]
Kind = Literal["reporting", "identification", "qualification"]


@dataclass(frozen=True)
class _Row:
    percent: str
    tdi: str | None = None
    tdi_unit: Literal["mg", "ug"] | None = None


# (upper bound in mg, inclusive?) -> row. Bands are checked in order.
_Band = tuple[Decimal | None, bool, _Row]  # (upper_mg, upper_inclusive, row); None = no upper bound

Q3A: dict[Kind, list[_Band]] = {
    "reporting": [(D(2000), True, _Row("0.05")), (None, False, _Row("0.03"))],
    "identification": [(D(2000), True, _Row("0.10", "1.0", "mg")), (None, False, _Row("0.05"))],
    "qualification": [(D(2000), True, _Row("0.15", "1.0", "mg")), (None, False, _Row("0.05"))],
}
Q3B: dict[Kind, list[_Band]] = {
    "reporting": [(D(1000), True, _Row("0.1")), (None, False, _Row("0.05"))],
    "identification": [
        (D(1), False, _Row("1.0", "5", "ug")),
        (D(10), True, _Row("0.5", "20", "ug")),
        (D(2000), True, _Row("0.2", "2", "mg")),
        (None, False, _Row("0.10")),
    ],
    "qualification": [
        (D(10), False, _Row("1.0", "50", "ug")),
        (D(100), True, _Row("0.5", "200", "ug")),
        (D(2000), True, _Row("0.2", "3", "mg")),
        (None, False, _Row("0.15")),
    ],
}

_UNIT_PER_MG = {"mg": D(1), "ug": D(1000)}


class ImpurityObservation(BaseModel):
    name: str = Field(description="Impurity code or name, e.g. 'Imp-B' or 'RRT 0.83'.")
    level_percent: float = Field(ge=0, le=100, description="Raw analytical result, % w/w relative to the drug substance.")
    is_degradant: bool | None = Field(
        None, description="For drug products: True if a degradation product. Q3B covers degradation products only."
    )


class Threshold(BaseModel):
    kind: Kind
    percent: str = Field(description="Percent threshold as written in the guideline, e.g. '0.10'.")
    tdi: str | None = Field(None, description="Total-daily-intake threshold, if the guideline gives one.")
    tdi_unit: Literal["mg", "ug"] | None = None
    binding: Literal["percent", "tdi"] = Field(description="Which limb of 'whichever is lower' applies at this dose.")
    binding_text: str = Field(description="The threshold that applies, e.g. '1.0 mg TDI' or '0.10%'.")
    percent_equivalent: float = Field(description="The binding threshold expressed as % of the dose.")


class ThresholdCheck(BaseModel):
    threshold: str
    compared_value: str = Field(description="The value actually compared, after guideline rounding.")
    exceeded: bool


class ImpurityAssessment(BaseModel):
    name: str
    raw_percent: float
    reported_percent: str | None = Field(description="Reported result after rounding; null = below reporting threshold.")
    tdi_mg: float = Field(description="Total daily intake, mg, from the reported (or raw, if not reported) result.")
    identification: ThresholdCheck
    qualification: ThresholdCheck
    next_action: Literal["none", "report", "identify", "identify_and_qualify"]


class ImpurityThresholdResult(GxPResult):
    product_type: ProductType
    max_daily_dose_mg: float
    thresholds: list[Threshold]
    impurities: list[ImpurityAssessment] = Field(default_factory=list)


def _row(table: dict[Kind, list[_Band]], kind: Kind, mdd_mg: Decimal) -> _Row:
    for upper, inclusive, row in table[kind]:
        if upper is None or mdd_mg < upper or (inclusive and mdd_mg == upper):
            return row
    raise AssertionError("unreachable")


def _threshold(kind: Kind, row: _Row, mdd_mg: Decimal) -> Threshold:
    pct = D(row.percent)
    if row.tdi is None:
        return Threshold(kind=kind, percent=row.percent, binding="percent",
                         binding_text=f"{row.percent}%", percent_equivalent=float(pct))
    tdi_mg = D(row.tdi) / _UNIT_PER_MG[row.tdi_unit]
    pct_as_mg = pct / 100 * mdd_mg
    if tdi_mg < pct_as_mg:
        unit = "µg" if row.tdi_unit == "ug" else "mg"
        return Threshold(kind=kind, percent=row.percent, tdi=row.tdi, tdi_unit=row.tdi_unit, binding="tdi",
                         binding_text=f"{row.tdi} {unit} TDI",
                         percent_equivalent=float(tdi_mg / mdd_mg * 100))
    return Threshold(kind=kind, percent=row.percent, tdi=row.tdi, tdi_unit=row.tdi_unit, binding="percent",
                     binding_text=f"{row.percent}%", percent_equivalent=float(pct))


def _report_decimals(product_type: ProductType, raw: Decimal, reporting: Threshold) -> int:
    if raw >= 1:
        return 1
    return 2 if product_type == "drug_substance" else decimals_of(reporting.percent)


def _check(th: Threshold, value_pct: Decimal, mdd_mg: Decimal) -> ThresholdCheck:
    if th.binding == "percent":
        cmp = round_half_up(value_pct, decimals_of(th.percent))
        return ThresholdCheck(threshold=th.binding_text, compared_value=f"{cmp}%", exceeded=cmp > D(th.percent))
    tdi_in_unit = value_pct / 100 * mdd_mg * _UNIT_PER_MG[th.tdi_unit]
    cmp = round_half_up(tdi_in_unit, decimals_of(th.tdi))
    unit = "µg" if th.tdi_unit == "ug" else "mg"
    return ThresholdCheck(threshold=th.binding_text, compared_value=f"{cmp} {unit}", exceeded=cmp > D(th.tdi))


def assess_impurity_thresholds(
    product_type: ProductType,
    max_daily_dose: float,
    dose_unit: Literal["mg", "g"],
    impurities: list[ImpurityObservation] | None = None,
    detail: Literal["concise", "full"] = "full",
) -> ImpurityThresholdResult:
    if max_daily_dose <= 0:
        raise GxPInputError("max_daily_dose must be > 0. Give the maximum daily dose of drug substance.")
    mdd_mg = D(max_daily_dose) * (1000 if dose_unit == "g" else 1)
    table, gl, url = (Q3A, "ICH Q3A(R2)", Q3A_URL) if product_type == "drug_substance" else (Q3B, "ICH Q3B(R2)", Q3B_URL)

    ths = {k: _threshold(k, _row(table, k, mdd_mg), mdd_mg) for k in ("reporting", "identification", "qualification")}
    findings: list[Finding] = []
    rows: list[ImpurityAssessment] = []

    for imp in impurities or []:
        if product_type == "drug_product" and imp.is_degradant is False:
            findings.append(Finding(level="warning", code="Q3B_SCOPE",
                                    message=f"{imp.name}: Q3B thresholds apply to degradation products; process "
                                            "impurities of the drug substance are controlled under Q3A."))
        raw = D(imp.level_percent)
        rep = ths["reporting"]
        reported = round_half_up(raw, _report_decimals(product_type, raw, rep))
        is_reported = round_half_up(raw, decimals_of(rep.percent)) > D(rep.percent)
        basis = reported if is_reported else raw
        idc = _check(ths["identification"], basis, mdd_mg)
        qc = _check(ths["qualification"], basis, mdd_mg)
        action = ("identify_and_qualify" if qc.exceeded else "identify" if idc.exceeded
                  else "report" if is_reported else "none")
        if qc.exceeded:
            findings.append(Finding(level="action_required", code="ABOVE_QUALIFICATION",
                                    message=f"{imp.name} ({qc.compared_value}) exceeds the qualification threshold "
                                            f"({qc.threshold}): identify and qualify, or reduce the level."))
        elif idc.exceeded:
            findings.append(Finding(level="action_required", code="ABOVE_IDENTIFICATION",
                                    message=f"{imp.name} ({idc.compared_value}) exceeds the identification "
                                            f"threshold ({idc.threshold}): structure must be identified."))
        rows.append(ImpurityAssessment(
            name=imp.name, raw_percent=imp.level_percent,
            reported_percent=str(reported) if is_reported else None,
            tdi_mg=float(basis / 100 * mdd_mg), identification=idc, qualification=qc, next_action=action))

    n_flag = sum(r.next_action in ("identify", "identify_and_qualify") for r in rows)
    summary = (f"{gl} at a maximum daily dose of {mdd_mg.normalize():f} mg: reporting {ths['reporting'].binding_text}, "
               f"identification {ths['identification'].binding_text}, qualification {ths['qualification'].binding_text}.")
    if rows:
        summary += f" {n_flag} of {len(rows)} impurities need identification or qualification."
    if detail == "concise":
        rows = [r for r in rows if r.next_action != "none"]

    return ImpurityThresholdResult(
        summary=summary,
        findings=findings,
        assumptions=[
            "Results are % relative to the drug substance, assuming a response factor of 1 unless already corrected.",
            "Thresholds are the guideline defaults; lower thresholds may apply to unusually toxic impurities "
            "(e.g. mutagenic impurities under ICH M7).",
        ],
        citations=[
            Citation(guideline=gl, section="Attachment 1", rule="Reporting, identification and qualification thresholds by "
                     "maximum daily dose; '% or TDI, whichever is lower'.", url=url),
            Citation(guideline=gl, section="Attachment 2, note on threshold comparison",
                     rule="Compare the rounded reported result (or TDI from it) with the threshold at the "
                          "threshold's decimal places; exceeded means greater than.", url=url),
        ],
        provenance=provenance("cmc_impurity_thresholds", gl.removeprefix("ICH "),
                              dict(product_type=product_type, mdd=max_daily_dose, unit=dose_unit,
                                   impurities=[i.model_dump() for i in impurities or []]),
                              ["ICH Q3A(R2) Att.2 examples 1-2" if product_type == "drug_substance"
                               else "ICH Q3B(R2) Att.2 examples 1-2"]),
        product_type=product_type,
        max_daily_dose_mg=float(mdd_mg),
        thresholds=list(ths.values()),
        impurities=rows,
    )
