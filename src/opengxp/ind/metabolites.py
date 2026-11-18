"""Metabolites in safety testing: ICH M3(R2) section 3 and its Q&A (questions 1-2, June 2011)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Finding, GxPInputError, GxPResult, provenance
from opengxp.ind.common import M3_QA_URL, M3_URL, cite, sig


class MetaboliteInput(BaseModel):
    name: str
    human_auc: float = Field(gt=0, description="Human group-mean AUC of the metabolite at steady state (clinical dose).")
    percent_of_total: float | None = Field(None, ge=0, le=100, description="% of total drug-related exposure (AUC). "
                                                                           "Computed from total_drug_related_auc if omitted.")
    animal_auc: dict[str, float] = Field(default_factory=dict, description="Metabolite AUC at the highest tox exposure per "
                                                                           "species, e.g. {'rat': 120, 'dog': 40}. Same units.")
    concern: Literal["none", "not_of_toxicological_concern", "unique_or_cause_for_concern"] = Field(
        "none", description="'not_of_toxicological_concern' e.g. most glutathione conjugates; "
                            "'unique_or_cause_for_concern' e.g. a unique human metabolite.")


class MetaboliteAssessment(BaseModel):
    name: str
    percent_of_total: float
    above_10_percent: bool
    majority: bool = Field(description="> 50% of total human drug-related exposure.")
    best_species: str | None
    animal_to_human_ratio: float | None
    required_ratio: str
    covered: bool | None
    action: str


class MetaboliteCoverageResult(GxPResult):
    metabolites: list[MetaboliteAssessment]


def metabolite_coverage(metabolites: list[MetaboliteInput], total_drug_related_auc: float | None = None,
                        daily_dose_mg: float | None = None) -> MetaboliteCoverageResult:
    if not metabolites:
        raise GxPInputError("Give at least one metabolite.")
    findings: list[Finding] = []
    rows: list[MetaboliteAssessment] = []
    for m in metabolites:
        pct = m.percent_of_total
        if pct is None:
            if not total_drug_related_auc:
                raise GxPInputError(f"{m.name}: give percent_of_total or total_drug_related_auc.")
            pct = m.human_auc / total_drug_related_auc * 100
        above, majority = pct > 10, pct > 50
        best = max(m.animal_auc.items(), key=lambda kv: kv[1]) if m.animal_auc else None
        ratio = best[1] / m.human_auc if best else None
        need = "animal > human (metabolite is the majority of exposure)" if majority else "animal ≥ 50% of human"
        if m.concern == "not_of_toxicological_concern":
            covered, action = None, "Not of toxicological concern (e.g. glutathione conjugate): testing not warranted."
        elif not above and m.concern == "none":
            covered, action = None, "≤ 10% of total drug-related exposure: nonclinical characterization not warranted."
        else:
            if ratio is None:
                covered = False
            else:
                covered = ratio > 1 if majority else ratio >= 0.5
            if covered:
                action = f"Covered by {best[0]} ({sig(ratio, 3)}× human exposure)."
            else:
                action = ("Not covered in any tox species: characterize the metabolite nonclinically (e.g. dose a species "
                          "that forms it, or the metabolite itself) before Phase III.")
                findings.append(Finding(level="action_required", code="METABOLITE_NOT_COVERED",
                                        message=f"{m.name} ({sig(pct, 3)}% of total): {action}"))
            if m.concern == "unique_or_cause_for_concern":
                findings.append(Finding(level="warning", code="CASE_BY_CASE",
                                        message=f"{m.name}: identified cause for concern (e.g. unique human metabolite); "
                                                "consider characterization case by case regardless of the 10% trigger."))
        rows.append(MetaboliteAssessment(name=m.name, percent_of_total=sig(pct), above_10_percent=above, majority=majority,
                                         best_species=best[0] if best else None,
                                         animal_to_human_ratio=sig(ratio) if ratio is not None else None,
                                         required_ratio=need, covered=covered, action=action))
    if daily_dose_mg is not None and daily_dose_mg < 10:
        findings.append(Finding(level="info", code="LOW_DOSE",
                                message="Daily dose < 10 mg: greater fractions of drug-related material might be more "
                                        "appropriate triggers than 10% (M3(R2) section 3)."))
    gaps = [r.name for r in rows if r.covered is False]
    summary = (f"{sum(r.above_10_percent for r in rows)} of {len(rows)} metabolites exceed 10% of total drug-related exposure; "
               + (f"not covered: {', '.join(gaps)}." if gaps else "all of these are covered by a tox species."))
    return MetaboliteCoverageResult(
        summary=summary, findings=findings,
        assumptions=["Exposures are group-mean AUCs (e.g. AUC0-inf) in the same units; total exposure, not unbound.",
                     "Animal AUC is the highest exposure reached in the toxicity studies."],
        citations=[cite("ICH M3(R2)", "Section 3", ">10% of total drug-related exposure and significantly greater in humans "
                        "than in tox studies triggers characterization, before Phase III.", M3_URL),
                   cite("ICH M3(R2) Q&A (R2)", "Questions 1-2 (June 2011)", "Adequate when animal exposure is ≥50% of human; "
                        "exceed human when the metabolite is the majority of exposure; 10% from group-mean AUC.", M3_QA_URL)],
        provenance=provenance("ind_metabolite_coverage", "ICH M3(R2) and Q&A (R2)",
                              {"metabolites": [m.model_dump() for m in metabolites],
                               "total_drug_related_auc": total_drug_related_auc, "daily_dose_mg": daily_dose_mg},
                              ["M3(R2) Q&A 1-2 thresholds; hand-computed cases"]),
        metabolites=rows)
