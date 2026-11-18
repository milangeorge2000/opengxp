"""ICH Q1A(R2) storage conditions (sections 2.1.7 and 2.2.7) and WHO climatic-zone long-term conditions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from opengxp.core import Citation, GxPInputError, GxPResult, provenance

Q1A_URL = "https://database.ich.org/sites/default/files/Q1A%28R2%29%20Guideline.pdf"
Material = Literal["drug_substance", "drug_product"]
Case = Literal["general", "impermeable_container", "semi_permeable_container", "refrigerator", "freezer", "below_minus_20C"]
LongTermChoice = Literal["25C/60RH", "30C/65RH", "25C/40RH", "30C/35RH"]
Zone = Literal["I", "II", "III", "IVa", "IVb"]

# WHO TRS 1010 Annex 10 (2018), long-term testing conditions by climatic zone
WHO_ZONES = {"I": "21°C ± 2°C/45% RH ± 5% RH", "II": "25°C ± 2°C/60% RH ± 5% RH",
             "III": "30°C ± 2°C/35% RH ± 5% RH", "IVa": "30°C ± 2°C/65% RH ± 5% RH",
             "IVb": "30°C ± 2°C/75% RH ± 5% RH"}

SIGNIFICANT_CHANGE_PRODUCT = [
    "A 5% change in assay from its initial value; or failure to meet potency criteria (biological/immunological procedures)",
    "Any degradation product exceeding its acceptance criterion",
    "Failure to meet acceptance criteria for appearance, physical attributes and functionality "
    "(some physical changes, e.g. softening of suppositories, melting of creams, may be expected at accelerated)",
    "As appropriate for the dosage form: failure to meet the pH acceptance criterion",
    "As appropriate for the dosage form: failure to meet dissolution acceptance criteria for 12 dosage units",
]


class Condition(BaseModel):
    study: Literal["long_term", "intermediate", "accelerated"]
    condition: str
    minimum_months_at_submission: int


class StorageConditionResult(GxPResult):
    material: Material
    case: Case
    conditions: list[Condition]
    significant_change_definition: list[str]
    notes: list[str]
    who_zone_long_term: str | None = None


def storage_conditions(material: Material, case: Case = "general", long_term: LongTermChoice | None = None,
                       climatic_zone: Zone | None = None) -> StorageConditionResult:
    notes: list[str] = []
    sec = "2.1.7" if material == "drug_substance" else "2.2.7"
    if case in ("impermeable_container", "semi_permeable_container") and material == "drug_substance":
        raise GxPInputError("Container-specific cases apply to drug products only.")
    if case == "general" or case == "impermeable_container":
        lt = long_term or "25C/60RH"
        if lt not in ("25C/60RH", "30C/65RH"):
            raise GxPInputError("General case long_term must be '25C/60RH' or '30C/65RH'.")
        conds = [Condition(study="long_term", condition="25°C ± 2°C/60% RH ± 5% RH" if lt == "25C/60RH" else "30°C ± 2°C/65% RH ± 5% RH",
                           minimum_months_at_submission=12)]
        if lt == "25C/60RH":
            conds.append(Condition(study="intermediate", condition="30°C ± 2°C/65% RH ± 5% RH", minimum_months_at_submission=6))
            notes.append("Intermediate testing is needed if significant change occurs at accelerated within 6 months; "
                         "submit at least 6 months from a 12-month intermediate study.")
        else:
            notes.append("With 30°C/65% RH as long-term condition there is no intermediate condition.")
        conds.append(Condition(study="accelerated", condition="40°C ± 2°C/75% RH ± 5% RH", minimum_months_at_submission=6))
        if case == "impermeable_container":
            notes.append("Impermeable containers: studies may be run at any controlled or ambient humidity (Q1A 2.2.7.2).")
        section = f"{sec}.1" if case == "general" else "2.2.7.2"
    elif case == "semi_permeable_container":
        lt = long_term or "25C/40RH"
        if lt not in ("25C/40RH", "30C/35RH"):
            raise GxPInputError("Semi-permeable case long_term must be '25C/40RH' or '30C/35RH'.")
        conds = [Condition(study="long_term", condition="25°C ± 2°C/40% RH ± 5% RH" if lt == "25C/40RH" else "30°C ± 2°C/35% RH ± 5% RH",
                           minimum_months_at_submission=12)]
        if lt == "25C/40RH":
            conds.append(Condition(study="intermediate", condition="30°C ± 2°C/65% RH ± 5% RH", minimum_months_at_submission=6))
        conds.append(Condition(study="accelerated", condition="40°C ± 2°C/NMT 25% RH", minimum_months_at_submission=6))
        notes += ["A 5% loss in water from initial after an equivalent of 3 months at 40°C/NMT 25% RH is a significant change.",
                  "Water-loss alone at accelerated does not trigger intermediate testing.",
                  "Water-loss rate ratios (alternative -> reference RH): 60->25: 1.9; 60->40: 1.5; 65->35: 1.9; 75->25: 3.0."]
        section = "2.2.7.3"
    elif case == "refrigerator":
        conds = [Condition(study="long_term", condition="5°C ± 3°C", minimum_months_at_submission=12),
                 Condition(study="accelerated", condition="25°C ± 2°C/60% RH ± 5% RH", minimum_months_at_submission=6)]
        notes.append("Significant change at accelerated between 3 and 6 months: base shelf life on long-term data; "
                     "within the first 3 months: discuss short-term excursions.")
        section = f"{sec}.2" if material == "drug_substance" else "2.2.7.4"
    elif case == "freezer":
        conds = [Condition(study="long_term", condition="-20°C ± 5°C", minimum_months_at_submission=12)]
        notes.append("Test one batch at an elevated temperature (e.g. 5°C or 25°C) to cover short excursions.")
        section = f"{sec}.3" if material == "drug_substance" else "2.2.7.5"
    else:
        conds = []
        notes.append("Storage below -20°C: case by case.")
        section = f"{sec}.4" if material == "drug_substance" else "2.2.7.6"

    sig = (["Failure to meet its specification"] if material == "drug_substance" else SIGNIFICANT_CHANGE_PRODUCT)
    zone = WHO_ZONES[climatic_zone] if climatic_zone else None
    cites = [Citation(guideline="ICH Q1A(R2)", section=section, rule="Storage conditions and minimum duration at submission.", url=Q1A_URL)]
    if zone:
        cites.append(Citation(guideline="WHO TRS 1010 Annex 10 (2018)", section="Appendix 1, climatic zones",
                              rule="Long-term testing conditions by climatic zone.", url=None))
    return StorageConditionResult(
        summary=f"{material.replace('_', ' ')} / {case.replace('_', ' ')}: "
                + "; ".join(f"{c.study.replace('_', ' ')} {c.condition} ({c.minimum_months_at_submission} mo)" for c in conds)
                + (f". WHO zone {climatic_zone} long-term: {zone}." if zone else "."),
        citations=cites,
        provenance=provenance("stab_storage_conditions", "Q1A(R2)", dict(m=material, c=case, lt=long_term, z=climatic_zone),
                              ["Q1A(R2) 2.1.7/2.2.7 tables transcription"]),
        material=material, case=case, conditions=conds, significant_change_definition=sig, notes=notes,
        who_zone_long_term=zone,
    )
