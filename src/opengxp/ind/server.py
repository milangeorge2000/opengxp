"""MCP server: IND-enabling decisions (ICH M12, FDA 2005 MRSD, ICH S9, ICH E14/S7B Q&A, ICH M3(R2)).
Offline, deterministic, read-only."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from opengxp.core import READ_ONLY_OFFLINE
from opengxp.ind.ddi import (
    KDEG_GUT_CYP3A4,
    KDEG_HEPATIC,
    DDIRiskResult,
    EnzymeData,
    Perpetrator,
    TransporterData,
    assess_ddi_risk,
)
from opengxp.ind.fih import HUMAN_KM, TABLE1, FIHDoseResult, ToxStudy, first_in_human_dose
from opengxp.ind.margins import ExposureInput, HergInput, SafetyMarginResult, safety_margins
from opengxp.ind.metabolites import MetaboliteCoverageResult, MetaboliteInput, metabolite_coverage
from opengxp.ind.tox_duration import Region, ToxDurationResult, tox_duration

ind = FastMCP(
    "opengxp-ind",
    instructions=(
        "Deterministic calculators for the nonclinical package that supports an IND / CTA and later phases. "
        "ind_ddi_risk: can in vitro data exclude a clinical drug interaction (ICH M12)? "
        "ind_first_in_human_dose: maximum recommended starting dose (FDA 2005) or oncology start dose (ICH S9). "
        "ind_safety_margins: hERG margin (ICH E14/S7B Q&A) and exposure margins at the NOAEL. "
        "ind_tox_duration: minimum repeated-dose tox duration for a trial or for marketing (ICH M3(R2)). "
        "ind_metabolite_coverage: are human metabolites >10% of exposure covered in tox species (ICH M3(R2), MIST)? "
        "Every result cites the clause used, lists assumptions and findings, and carries a provenance hash. "
        "Decision support, not regulatory advice."
    ),
    mask_error_details=True,
)


@ind.tool(
    title="Drug interaction risk from in vitro data (ICH M12)",
    tags={"ind", "dmpk", "ich-m12", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH M12 (2024)"], "validated_against": "M12 2.1-2.2 cutoffs; Table 3 and Table 6"},
    version="1.0",
    timeout=30,
)
def ind_ddi_risk(
    perpetrator: Annotated[Perpetrator, Field(description="The investigational drug: steady-state Cmax at the highest dose, "
                                                           "fu,p, dose, molecular weight, route.")],
    enzymes: Annotated[list[EnzymeData] | None, Field(max_length=20, description="Per CYP/UGT: Ki,u; KI,u + kinact; "
                       "induction donors or Emax/EC50; optional victim fm for the mechanistic static AUCR.")] = None,
    transporters: Annotated[list[TransporterData] | None, Field(max_length=20, description="Per transporter: unbound IC50.")] = None,
) -> DDIRiskResult:
    """Decide, per enzyme and transporter, whether ICH M12 lets in vitro data exclude a clinical interaction with the
    investigational drug as precipitant.

    Applies the basic methods (Cmax,u/Ki,u < 0.02; gut CYP3A (Dose/250 mL)/Ki,u < 10; TDI (kobs+kdeg)/kdeg < 1.25;
    mRNA ≥ 2-fold below 50×Cmax,u; transporter Table 1 cutoffs), then the basic kinetic induction model (R > 0.8)
    and the mechanistic static model (AUCR 0.80-1.25, inhibition and induction also evaluated alone) when data are
    given. Applies the fu,p 1% floor and the M12 default Fa, Fg, ka. Returns each check, a conclusion per target and
    the next step. Not for the drug as a victim, and not a PBPK model.
    """
    return assess_ddi_risk(perpetrator, enzymes, transporters)


@ind.tool(
    title="First-in-human starting dose (FDA 2005 MRSD / ICH S9)",
    tags={"ind", "toxicology", "fih", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["FDA MRSD guidance (2005)", "ICH S9"], "validated_against": "FDA 2005 Table 1; Appendix C example"},
    version="1.0",
    timeout=30,
)
def ind_first_in_human_dose(
    studies: Annotated[list[ToxStudy], Field(min_length=1, max_length=20, description="One entry per pivotal tox study: "
                       "species and NOAEL (or STD10 / HNSTD for oncology), mg/kg/day.")],
    population: Annotated[Literal["healthy_volunteers", "oncology"], Field(description="healthy_volunteers: FDA 2005 MRSD "
                          "from the NOAEL. oncology: ICH S9, 1/10 STD10 (rodent) or 1/6 HNSTD (non-rodent).")] = "healthy_volunteers",
    safety_factor: Annotated[float, Field(gt=0, description="Divides the HED (healthy volunteers). Default 10; below 10 needs a justification.")] = 10.0,
    safety_factor_justification: str | None = None,
    scaling: Annotated[Literal["body_surface_area", "mg_per_kg"], Field(description="body_surface_area (default, FDA Table 1). "
                       "mg_per_kg only with a justification meeting FDA 2005 V.B.")] = "body_surface_area",
    scaling_justification: str | None = None,
    most_appropriate_species: Annotated[str | None, Field(description="Override the default (most sensitive species); needs species_justification.")] = None,
    species_justification: str | None = None,
    human_weight_kg: Annotated[float, Field(gt=0, description="For the mg total; FDA assumes 60 kg.")] = 60.0,
    high_risk_biologic: Annotated[bool, Field(description="True for agonists / immune activators etc.: flags that a MABEL is needed.")] = False,
) -> FIHDoseResult:
    """Maximum recommended starting dose for a first-in-human trial: human equivalent dose for every species
    (FDA 2005 Table 1 or the 0.33-exponent weight formula), the selected species and why, and the MRSD in mg/kg
    and mg. Oncology mode applies ICH S9 Note 2. Does not compute MABEL / PAD.
    """
    return first_in_human_dose(studies, population, safety_factor, safety_factor_justification, scaling,
                               scaling_justification, most_appropriate_species, species_justification, human_weight_kg,
                               high_risk_biologic)


@ind.tool(
    title="hERG and exposure safety margins (ICH E14/S7B Q&A, ICH M3(R2))",
    tags={"ind", "safety-pharmacology", "ich-s7b", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH E14/S7B Q&A (2022)", "ICH M3(R2)"], "validated_against": "S7B Q&A 1.2 method"},
    version="1.0",
    timeout=30,
)
def ind_safety_margins(
    herg: Annotated[HergInput | None, Field(description="hERG IC50, high clinical Cmax,ss (total), fu,p, and your assay's threshold.")] = None,
    exposures: Annotated[list[ExposureInput] | None, Field(max_length=20, description="Animal AUC / Cmax at the NOAEL per species.")] = None,
    human_auc: Annotated[float | None, Field(gt=0, description="Human AUC at the intended dose (same units as animal AUC).")] = None,
    human_cmax: Annotated[float | None, Field(gt=0, description="Human Cmax at the intended dose (same units as animal Cmax).")] = None,
) -> SafetyMarginResult:
    """hERG safety margin (IC50 / free high clinical Cmax,ss with the 1% fu,p floor), compared with a lab-calibrated
    threshold if given, and animal-to-human exposure margins at the NOAEL. Flags margins below 1 and states that
    there is no universal hERG cut-off.
    """
    return safety_margins(herg, exposures, human_auc, human_cmax)


@ind.tool(
    title="Repeated-dose toxicity study duration (ICH M3(R2))",
    tags={"ind", "toxicology", "ich-m3", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH M3(R2)"], "validated_against": "M3(R2) Tables 1 and 2, every row"},
    version="1.0",
    timeout=30,
)
def ind_tox_duration(
    duration: Annotated[float, Field(gt=0, description="Planned maximum clinical trial duration, or indicated treatment duration for marketing.")],
    unit: Literal["days", "weeks", "months"],
    purpose: Annotated[Literal["clinical_trial", "marketing"], Field(description="clinical_trial: Table 1; marketing: Table 2.")] = "clinical_trial",
    regions: Annotated[list[Region] | None, Field(description="Regions to report (EU accepts 6-month non-rodent). Default all.")] = None,
    single_dose_trial: bool = False,
) -> ToxDurationResult:
    """Minimum rodent and non-rodent repeated-dose toxicity study durations for a planned clinical trial or for
    marketing, by region, with the applicable M3(R2) footnotes (3-month bridging, EU 6-month non-rodent, pediatric)."""
    return tox_duration(duration, unit, purpose, regions, single_dose_trial)


@ind.tool(
    title="Human metabolite coverage in tox species (ICH M3(R2), MIST)",
    tags={"ind", "dmpk", "toxicology", "ich-m3", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH M3(R2)", "ICH M3(R2) Q&A (R2)"], "validated_against": "M3(R2) Q&A questions 1-2"},
    version="1.0",
    timeout=30,
)
def ind_metabolite_coverage(
    metabolites: Annotated[list[MetaboliteInput], Field(min_length=1, max_length=50, description="Human metabolites with "
                           "their human AUC, % of total drug-related exposure, and AUC in each tox species.")],
    total_drug_related_auc: Annotated[float | None, Field(gt=0, description="Total drug-related AUC in humans (to compute %).")] = None,
    daily_dose_mg: Annotated[float | None, Field(gt=0, description="Clinical daily dose; < 10 mg triggers a note.")] = None,
) -> MetaboliteCoverageResult:
    """Which human metabolites exceed 10% of total drug-related exposure, and whether a tox species covers each
    (animal ≥ 50% of human exposure, or above human exposure when the metabolite is the majority)."""
    return metabolite_coverage(metabolites, total_drug_related_auc, daily_dose_mg)


@ind.resource("ich://m12/kdeg", mime_type="application/json", tags={"ich-m12"},
              description="ICH M12 Table 6: enzyme degradation rate constants (kdeg, /min) for TDI assessment.")
def m12_kdeg() -> dict:
    return {"hepatic": KDEG_HEPATIC, "intestinal_CYP3A4": KDEG_GUT_CYP3A4}


@ind.resource("fda://mrsd-2005/conversion-factors", mime_type="application/json", tags={"fih"},
              description="FDA 2005 Table 1: km and HED divisors by species (60 kg human).")
def mrsd_table1() -> dict:
    return {"human_km": HUMAN_KM, "species": {k: {"km": v[0], "divide_animal_dose_by": v[1]} for k, v in TABLE1.items()}}


@ind.prompt(description="Draft an IND nonclinical decision summary (Module 2.4 style) using the ind tools.", tags={"ind"})
def ind_nonclinical_summary(drug_name: str, indication: str, planned_trial: str) -> str:
    return (
        f"Prepare a nonclinical decision summary for {drug_name} ({indication}); planned trial: {planned_trial}.\n"
        "1. ind_tox_duration for the planned trial duration.\n"
        "2. ind_first_in_human_dose with every pivotal tox study's NOAEL (or STD10/HNSTD for oncology).\n"
        "3. ind_safety_margins with the hERG IC50, the projected clinical Cmax and the NOAEL exposures.\n"
        "4. ind_ddi_risk with the in vitro CYP, UGT and transporter data.\n"
        "5. ind_metabolite_coverage once human metabolite data exist.\n"
        "Write tables, quote numbers exactly as returned, cite each result's guideline section, put action_required "
        "findings first, and end with the disclaimer."
    )
