"""ICH M3(R2) Tables 1 and 2: minimum duration of repeated-dose toxicity studies (rodent, non-rodent)."""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Finding, GxPInputError, GxPResult, provenance
from opengxp.ind.common import M3_URL, cite

DAYS = {"days": Fraction(1), "weeks": Fraction(7), "months": Fraction(36525, 1200)}  # month = 365.25/12 days
TWO_WEEKS, ONE_MONTH, THREE_MONTHS, SIX_MONTHS = Fraction(14), DAYS["months"], 3 * DAYS["months"], 6 * DAYS["months"]
Region = Literal["US", "EU", "JP"]


class RegionDuration(BaseModel):
    region: Region
    rodent: str
    non_rodent: str


class ToxDurationResult(GxPResult):
    purpose: str
    clinical_duration_days: float
    table_row: str
    by_region: list[RegionDuration]
    footnotes: list[str] = Field(default_factory=list)


def _fmt_days(d: Fraction) -> str:
    if d % 7 == 0 and d < 28:
        return f"{int(d / 7)} weeks"
    return f"{float(d):g} days"


def tox_duration(duration: float, unit: Literal["days", "weeks", "months"],
                 purpose: Literal["clinical_trial", "marketing"] = "clinical_trial",
                 regions: list[Region] | None = None, single_dose_trial: bool = False) -> ToxDurationResult:
    if duration <= 0:
        raise GxPInputError("duration must be > 0.")
    regions = regions or ["US", "EU", "JP"]
    d = Fraction(str(duration)) * DAYS[unit]
    foot: list[str] = []
    findings: list[Finding] = []
    same = f"same as the clinical trial ({duration:g} {unit})"

    if purpose == "clinical_trial":
        sec = "Table 1"
        if d <= TWO_WEEKS:
            row, rod, nonrod = "Up to 2 weeks", "2 weeks", "2 weeks"
            foot.append("a: Clinical studies of less than 14 days can be supported with toxicity studies of the same duration "
                        "as the proposed clinical study.")
            if single_dose_trial and "US" in regions:
                foot.append("a: In the US, extended single-dose toxicity studies (Table 3 footnote c) can support single-dose trials.")
        elif d <= SIX_MONTHS:
            row, rod, nonrod = "Between 2 weeks and 6 months", same, same
            if d > THREE_MONTHS:
                foot.append("b: Trials longer than 3 months can start with 3-month rodent and non-rodent data if complete "
                            "chronic data are available before dosing extends beyond 3 months.")
        else:
            row, rod, nonrod = "> 6 months", "6 months", "9 months"
            foot.append("b: Trials longer than 3 months can start with 3-month rodent and non-rodent data if complete "
                        "chronic data are available before dosing extends beyond 3 months.")
            foot.append("d: In the EU, 6-month non-rodent studies are acceptable. In the US and Japan, up to 6 months can be "
                        "appropriate e.g. for intermittent short-term use, chronic use to prevent cancer recurrence, "
                        "short life expectancy, or immunogenicity/intolerance confounding longer studies.")
        by = [RegionDuration(region=r, rodent=rod,
                             non_rodent="6 months" if (r == "EU" and nonrod == "9 months") else nonrod) for r in regions]
    else:
        sec = "Table 2"
        if d <= TWO_WEEKS:
            row, rod, nonrod = "Up to 2 weeks", "1 month", "1 month"
        elif d <= ONE_MONTH:
            row, rod, nonrod = ">2 weeks to 1 month", "3 months", "3 months"
        elif d <= THREE_MONTHS:
            row, rod, nonrod = ">1 month to 3 months", "6 months", "6 months"
            findings.append(Finding(level="info", code="WIDESPREAD_LONG_TERM_USE",
                                    message="If clinical experience suggests widespread use beyond the indicated duration "
                                            "(e.g. anxiety, seasonal allergic rhinitis, pain), the >3-month row may be more "
                                            "appropriate (M3(R2) 5.2)."))
        else:
            row, rod, nonrod = ">3 months", "6 months", "9 months"
            foot.append("d: In the EU, 6-month non-rodent studies are acceptable.")
        by = [RegionDuration(region=r, rodent=rod,
                             non_rodent="6 months" if (r == "EU" and nonrod == "9 months") else nonrod) for r in regions]

    foot.append("c: Where children are the primary population and animal data show developmental concerns, juvenile "
                "toxicity studies may be needed (M3(R2) section 12).")
    findings.append(Finding(level="info", code="TWO_SPECIES",
                            message="Repeated-dose studies in two mammalian species, one a non-rodent. Exploratory "
                                    "(microdose) trials follow M3(R2) Table 3 instead; anticancer drugs follow ICH S9; "
                                    "biotechnology products follow ICH S6(R1)."))
    eu = next((b for b in by if b.region == "EU"), by[0])
    summary = (f"{'Clinical trial' if purpose == 'clinical_trial' else 'Marketing'} duration {duration:g} {unit} "
               f"({row}): rodent {rod}, non-rodent {nonrod}"
               + (f" (EU: {eu.non_rodent})" if eu.non_rodent != nonrod else "") + ".")
    return ToxDurationResult(
        summary=summary, findings=findings,
        assumptions=["Durations compared in days (1 month = 30.44 days, 6 months = 182.6 days).",
                     "Supports clinical development of small molecules under ICH M3(R2)."],
        citations=[cite("ICH M3(R2)", f"Section 5, {sec}", "Minimum duration of repeated-dose toxicity studies.", M3_URL)],
        provenance=provenance("ind_tox_duration", "ICH M3(R2) (2009)",
                              {"duration": duration, "unit": unit, "purpose": purpose, "regions": regions,
                               "single_dose_trial": single_dose_trial}, ["M3(R2) Tables 1 and 2, every row"]),
        purpose=purpose, clinical_duration_days=round(float(d), 2), table_row=row, by_region=by, footnotes=foot)
