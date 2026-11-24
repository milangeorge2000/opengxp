"""Excipient checks against FDA Inactive Ingredient Database (IID) rows. Pure logic: the rows come from the
database (exact lookup), this module only compares and explains. No fuzzy matching happens here.

FDA records a limit for a route + dosage form either as the maximum potency per dosage unit (older records)
or as the maximum daily exposure in mg (newer records); a row has one or the other, or neither."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Citation, Finding, GxPResult, provenance

Unit = Literal["mg", "mg/mL", "%w/w", "%w/v", "%v/v"]
Verdict = Literal["within_precedent", "above_precedent", "not_comparable", "precedent_without_limit", "no_precedent"]

IID_URL = "https://www.fda.gov/drugs/drug-approvals-and-databases/inactive-ingredients-database-download"
_PER_VOLUME = re.compile(r"^(mg|ml)/(\d+(?:\.\d+)?)ml$", re.IGNORECASE)


class ExcipientInput(BaseModel):
    ingredient: str = Field(min_length=2, max_length=200,
                            description="IID ingredient name exactly as returned by ref_resolve_ingredient.")
    amount: float = Field(gt=0, description="Amount per dosage unit (or concentration) in `unit`.")
    unit: Unit = Field(description="mg per dosage unit, mg/mL, or %w/w, %w/v, %v/v. Never guessed.")


class DatasetInfo(BaseModel):
    label: str
    source_url: str
    source_sha256: str
    row_count: int
    published_at: str | None = None


class IIDLimit(BaseModel):
    row_no: int
    route: str
    dosage_form: str
    limit_kind: Literal["per_unit", "daily", "none"]
    fda_value: str | None = Field(None, description="Value as FDA lists it, with FDA's unit.")
    normalised_value: float | None = Field(None, description="FDA value converted to the input unit, when comparable.")
    normalised_unit: str | None = None
    yours: float | None = Field(None, description="Your amount in the same unit (daily mg for daily limits).")
    percent_of_limit: float | None = None
    comparison: Literal["within", "above", "not_comparable", "no_limit"]
    note: str | None = None


class ExcipientVerdict(BaseModel):
    ingredient: str
    unii: str | None = None
    route: str
    dosage_form: str
    verdict: Verdict
    limits: list[IIDLimit] = Field(default_factory=list)
    explanation: str


class ExcipientCheckResult(GxPResult):
    dataset: DatasetInfo
    excipients: list[ExcipientVerdict]


def normalise_iid_unit(value: Decimal, unit: str | None) -> tuple[Decimal, str] | None:
    """Convert an IID potency value to one of the input units, or None if it has no comparable unit."""
    if unit is None:
        return None
    u = unit.strip()
    if u.lower() == "mg":
        return value, "mg"
    if u in ("%w/w", "%w/v", "%v/v"):
        return value, u
    if u.lower() == "mg/mg":
        return value * 100, "%w/w"
    if m := _PER_VOLUME.match(u):
        per = Decimal(m.group(2))
        if per <= 0:
            return None
        return (value / per, "mg/mL") if m.group(1).lower() == "mg" else (value / per * 100, "%v/v")
    return None  # e.g. 'ADJ PH', 'ml'


def _fmt(d: Decimal | float) -> str:
    return f"{Decimal(str(d)).normalize():f}"


def compare_row(item: ExcipientInput, row: dict, units_per_day: float | None) -> IIDLimit:
    base = {"row_no": row["row_no"], "route": row["route"], "dosage_form": row["dosage_form"]}
    amount = Decimal(str(item.amount))
    if row.get("potency_amount") is not None:
        fda = Decimal(str(row["potency_amount"]))
        shown = f"{_fmt(fda)} {row.get('potency_unit') or ''}".strip()
        conv = normalise_iid_unit(fda, row.get("potency_unit"))
        if conv is None or conv[1] != item.unit:
            return IIDLimit(**base, limit_kind="per_unit", fda_value=shown, comparison="not_comparable",
                            note=f"FDA lists this limit in '{row.get('potency_unit')}', which cannot be compared with "
                                 f"'{item.unit}' without assumptions.")
        lim = conv[0]
        pct = float(amount / lim * 100) if lim > 0 else None
        return IIDLimit(**base, limit_kind="per_unit", fda_value=shown, normalised_value=float(lim),
                        normalised_unit=item.unit, yours=item.amount,
                        percent_of_limit=None if pct is None else round(pct, 1),
                        comparison="within" if amount <= lim else "above")
    if row.get("max_daily_exposure") is not None:
        fda = Decimal(str(row["max_daily_exposure"]))
        unit = (row.get("max_daily_exposure_unit") or "").strip()
        shown = f"{_fmt(fda)} {unit} per day".strip()
        if unit.lower() != "mg" or item.unit != "mg" or not units_per_day:
            why = ("give units_per_day so your daily amount can be compared" if item.unit == "mg" and unit.lower() == "mg"
                   else "a daily limit in mg can only be compared with an amount in mg per dosage unit")
            return IIDLimit(**base, limit_kind="daily", fda_value=shown, comparison="not_comparable", note=why)
        daily = amount * Decimal(str(units_per_day))
        pct = float(daily / fda * 100) if fda > 0 else None
        return IIDLimit(**base, limit_kind="daily", fda_value=shown, normalised_value=float(fda),
                        normalised_unit="mg/day", yours=float(daily),
                        percent_of_limit=None if pct is None else round(pct, 1),
                        comparison="within" if daily <= fda else "above")
    note = "pH adjuster: FDA lists no amount." if (row.get("potency_unit") or "").upper() == "ADJ PH" \
        else "FDA lists this use without a maximum amount."
    return IIDLimit(**base, limit_kind="none", comparison="no_limit", note=note)


def verdict_for(item: ExcipientInput, route: str, dosage_form: str, rows: list[dict],
                units_per_day: float | None) -> ExcipientVerdict:
    limits = [compare_row(item, r, units_per_day) for r in rows]
    unii = next((r.get("unii") for r in rows if r.get("unii")), None)
    head = dict(ingredient=item.ingredient.upper().strip(), unii=unii, route=route, dosage_form=dosage_form, limits=limits)
    if not rows:
        return ExcipientVerdict(**head, verdict="no_precedent",
                                explanation=f"No IID entry for {head['ingredient']} by the {route} route as "
                                            f"{dosage_form}. A level without precedent needs a safety justification.")
    kinds = {lim.comparison for lim in limits}
    if "within" in kinds:
        best = max((lim for lim in limits if lim.comparison == "within"), key=lambda lim: lim.normalised_value or 0)
        return ExcipientVerdict(**head, verdict="within_precedent",
                                explanation=f"Within FDA precedent: {best.percent_of_limit}% of {best.fda_value} "
                                            f"(IID row {best.row_no}).")
    if "above" in kinds:
        top = max((lim for lim in limits if lim.comparison == "above"), key=lambda lim: lim.normalised_value or 0)
        return ExcipientVerdict(**head, verdict="above_precedent",
                                explanation=f"Above the highest FDA precedent: {top.percent_of_limit}% of {top.fda_value} "
                                            f"(IID row {top.row_no}). Higher levels need justification.")
    if "not_comparable" in kinds:
        return ExcipientVerdict(**head, verdict="not_comparable",
                                explanation="FDA has precedent for this route and dosage form, but in a unit or form "
                                            "that cannot be compared with your input. See each limit's note.")
    return ExcipientVerdict(**head, verdict="precedent_without_limit",
                            explanation="FDA has approved this excipient for this route and dosage form without a "
                                        "listed maximum amount.")


def build_result(items: list[ExcipientInput], route: str, dosage_form: str, rows_by_item: list[list[dict]],
                 units_per_day: float | None, dataset: DatasetInfo) -> ExcipientCheckResult:
    verdicts = [verdict_for(i, route, dosage_form, rows, units_per_day) for i, rows in zip(items, rows_by_item)]
    findings: list[Finding] = []
    for v in verdicts:
        if v.verdict == "above_precedent":
            findings.append(Finding(level="action_required", code="ABOVE_IID_PRECEDENT", message=f"{v.ingredient}: {v.explanation}"))
        elif v.verdict == "no_precedent":
            findings.append(Finding(level="action_required", code="NO_IID_PRECEDENT", message=v.explanation))
        elif v.verdict == "not_comparable":
            findings.append(Finding(level="warning", code="IID_NOT_COMPARABLE", message=f"{v.ingredient}: {v.explanation}"))
    counts = {k: sum(v.verdict == k for v in verdicts) for k in
              ("within_precedent", "above_precedent", "no_precedent", "not_comparable", "precedent_without_limit")}
    summary = (f"{len(verdicts)} excipient(s) checked for {route} {dosage_form} against the FDA IID ({dataset.label}): "
               + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items() if n) + ".")
    inputs = {"route": route, "dosage_form": dosage_form, "units_per_day": units_per_day,
              "excipients": [i.model_dump() for i in items], "dataset_sha256": dataset.source_sha256}
    return ExcipientCheckResult(
        summary=summary, findings=findings,
        assumptions=[
            "Exact match on IID ingredient name, route and dosage form; resolve names first with ref_resolve_ingredient.",
            "Per-unit limits are compared only in the same unit (mg/N mL is converted to mg/mL; mg/mg to %w/w).",
            "Daily limits are compared as amount x units_per_day, only for amounts in mg.",
            "Exceeding IID precedent is not prohibited; it needs a safety justification.",
        ],
        citations=[Citation(guideline=f"FDA Inactive Ingredient Database, {dataset.label} release",
                            section="Rows listed under each excipient (row numbers refer to the FDA file)",
                            url=dataset.source_url)],
        provenance=provenance("ref_check_excipients", f"FDA IID {dataset.label} sha256:{dataset.source_sha256[:12]}",
                              inputs, ["IID Oct 2026 real-row fixture", "RLS tests on Postgres"]),
        dataset=dataset, excipients=verdicts,
    )
