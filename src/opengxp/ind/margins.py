"""Safety margins: hERG (ICH E14/S7B Q&A 1.2, 2022) and animal-to-human exposure margins at the NOAEL."""

from __future__ import annotations

from pydantic import BaseModel, Field

from opengxp.core import Finding, GxPInputError, GxPResult, provenance
from opengxp.ind.common import E14S7B_QA_URL, M3_URL, cite, effective_fu, sig


class HergInput(BaseModel):
    ic50_uM: float = Field(gt=0, description="hERG IC50 from a best-practice assay, µM.")
    cmax_ss_total_uM: float = Field(gt=0, description="High clinical exposure: mean steady-state Cmax at the maximum therapeutic "
                                                     "dose with the factor that raises it most (E14 Q&A 5.1), total, µM.")
    fu_p: float = Field(gt=0, le=1, description="Fraction unbound in plasma.")
    fu_p_reliable_below_1pct: bool = False
    protein_binding_reliable: bool = Field(True, description="False if binding could not be accurately assessed; "
                                                             "margins are then given for free and total Cmax.")
    threshold_fold: float | None = Field(None, gt=0, description="Your assay's low-risk threshold, set from reference drugs with "
                                                                "known TdP risk run under the same protocol.")
    threshold_source: str | None = Field(None, description="Where the threshold comes from (lab calibration report, publication).")


class ExposureInput(BaseModel):
    species: str
    study: str | None = None
    auc_at_noael: float | None = Field(None, gt=0, description="Animal AUC at the NOAEL (same units as the human AUC).")
    cmax_at_noael: float | None = Field(None, gt=0, description="Animal Cmax at the NOAEL (same units as the human Cmax).")


class ExposureMargin(BaseModel):
    species: str
    study: str | None
    auc_margin: float | None
    cmax_margin: float | None


class SafetyMarginResult(GxPResult):
    herg_margin_free: float | None = None
    herg_margin_total: float | None = None
    herg_low_risk: bool | None = Field(None, description="True if the free margin exceeds the given threshold; None without a threshold.")
    exposure_margins: list[ExposureMargin] = Field(default_factory=list)
    lowest_auc_margin: float | None = None


def safety_margins(herg: HergInput | None = None, exposures: list[ExposureInput] | None = None,
                   human_auc: float | None = None, human_cmax: float | None = None) -> SafetyMarginResult:
    if herg is None and not exposures:
        raise GxPInputError("Give herg inputs, exposures (with human_auc and/or human_cmax), or both.")
    findings: list[Finding] = []
    assumptions: list[str] = []
    cites = []
    out: dict = {}
    parts: list[str] = []

    if herg:
        fu, note = effective_fu(herg.fu_p, herg.fu_p_reliable_below_1pct)
        if note:
            assumptions.append(note)
        free = herg.ic50_uM / (herg.cmax_ss_total_uM * fu)
        out["herg_margin_free"] = sig(free)
        if not herg.protein_binding_reliable:
            out["herg_margin_total"] = sig(herg.ic50_uM / herg.cmax_ss_total_uM)
            findings.append(Finding(level="warning", code="BINDING_UNCERTAIN",
                                    message="Protein binding not reliably measured: both free and total-Cmax margins reported (Q&A 1.2)."))
        if herg.threshold_fold is None:
            findings.append(Finding(level="warning", code="NO_HERG_THRESHOLD",
                                    message="No threshold given. ICH E14/S7B Q&A 1.2 compares the margin with margins of reference "
                                            "drugs of known TdP risk under the same protocol; there is no universal cut-off. "
                                            "Commonly cited literature values (e.g. 30-fold) are not ICH thresholds."))
            parts.append(f"hERG margin (free) {sig(free, 3)}-fold")
        else:
            low = free > herg.threshold_fold
            out["herg_low_risk"] = low
            parts.append(f"hERG margin (free) {sig(free, 3)}-fold vs threshold {herg.threshold_fold:g} -> "
                         + ("above threshold (supports low TdP risk)" if low else "not above threshold"))
            if not low:
                findings.append(Finding(level="action_required", code="HERG_MARGIN_BELOW_THRESHOLD",
                                        message="hERG margin does not exceed the assay threshold; further nonclinical "
                                                "(e.g. in vivo QT, proarrhythmia models) and clinical QT assessment needed."))
            if not herg.threshold_source:
                findings.append(Finding(level="warning", code="THRESHOLD_UNSOURCED",
                                        message="State the source of the threshold; Q&A 1.2 expects supporting data in the study report."))
        cites.append(cite("ICH E14/S7B Q&A (2022)", "S7B Q&A 1.2",
                          "Margin = hERG IC50 / free high clinical Cmax,ss; fu,p floored at 1%; compare with reference-drug margins.",
                          E14S7B_QA_URL))

    if exposures:
        if human_auc is None and human_cmax is None:
            raise GxPInputError("Exposure margins need human_auc and/or human_cmax at the intended clinical dose.")
        rows = []
        for e in exposures:
            a = e.auc_at_noael / human_auc if e.auc_at_noael and human_auc else None
            c = e.cmax_at_noael / human_cmax if e.cmax_at_noael and human_cmax else None
            rows.append(ExposureMargin(species=e.species, study=e.study, auc_margin=sig(a) if a else None,
                                       cmax_margin=sig(c) if c else None))
            for kind, m in (("AUC", a), ("Cmax", c)):
                if m is not None and m < 1:
                    findings.append(Finding(level="action_required", code="EXPOSURE_BELOW_CLINICAL",
                                            message=f"{e.species}: {kind} at the NOAEL is below the clinical {kind} "
                                                    f"(margin {sig(m, 3)}); the clinical dose is not covered by this study."))
        out["exposure_margins"] = rows
        aucs = [r.auc_margin for r in rows if r.auc_margin is not None]
        if aucs:
            out["lowest_auc_margin"] = min(aucs)
            parts.append(f"lowest AUC margin at the NOAEL {min(aucs):g}-fold")
        assumptions.append("Animal and human exposures are in the same units and for the same analyte (usually total parent).")
        cites.append(cite("ICH M3(R2)", "Section 1.5", "Exposure margins usually from group mean AUC; 50-fold margin is an "
                          "acceptable top dose for general toxicity studies.", M3_URL))
        if aucs and min(aucs) >= 50:
            findings.append(Finding(level="info", code="AUC_MARGIN_GE_50",
                                    message="All AUC margins ≥ 50-fold: the 50-fold exposure limit dose of ICH M3(R2) 1.5 is met."))

    inputs = {"herg": herg.model_dump() if herg else None, "exposures": [e.model_dump() for e in exposures or []],
              "human_auc": human_auc, "human_cmax": human_cmax}
    return SafetyMarginResult(
        summary="; ".join(parts) + "." if parts else "Exposure margins computed.", findings=findings,
        assumptions=assumptions, citations=cites,
        provenance=provenance("ind_safety_margins", "ICH E14/S7B Q&A (2022); ICH M3(R2)", inputs, ["hand-computed cases"]),
        **out)
