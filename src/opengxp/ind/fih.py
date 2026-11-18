"""First-in-human starting dose.

healthy_volunteers: FDA guidance "Estimating the Maximum Safe Starting Dose in Initial Clinical Trials for
Therapeutics in Adult Healthy Volunteers" (2005): NOAEL -> HED (Table 1, body-surface-area scaling) ->
most appropriate species (default: lowest HED) -> divide by a safety factor (default 10) = MRSD.

oncology: ICH S9 Note 2: 1/10 of the STD10 in rodents, or 1/6 of the HNSTD when the non-rodent is the
most appropriate species, after conversion to a human equivalent dose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Finding, GxPInputError, GxPResult, provenance
from opengxp.ind.common import FDA_MRSD_URL, S9_URL, cite, sig

# FDA 2005 Table 1: (km, "divide animal dose by") for a 60 kg human
TABLE1: dict[str, tuple[float, float]] = {
    "mouse": (3, 12.3), "hamster": (5, 7.4), "rat": (6, 6.2), "ferret": (7, 5.3), "guinea_pig": (8, 4.6),
    "rabbit": (12, 3.1), "dog": (20, 1.8), "monkey": (12, 3.1), "marmoset": (6, 6.2),
    "squirrel_monkey": (7, 5.3), "baboon": (20, 1.8), "micro_pig": (27, 1.4), "mini_pig": (35, 1.1),
}
HUMAN_KM = 37
RODENTS = {"mouse", "rat", "hamster", "guinea_pig"}
ALIASES = {"cynomolgus": "monkey", "cyno": "monkey", "rhesus": "monkey", "nhp": "monkey", "minipig": "mini_pig",
           "mini-pig": "mini_pig", "guinea pig": "guinea_pig", "micro-pig": "micro_pig", "squirrel monkey": "squirrel_monkey",
           "beagle": "dog", "mice": "mouse", "rats": "rat", "dogs": "dog", "monkeys": "monkey"}


class ToxStudy(BaseModel):
    species: str = Field(description="e.g. rat, mouse, dog, monkey (cynomolgus), mini_pig, rabbit. Other species need body_weight_kg.")
    dose_mg_per_kg: float = Field(gt=0, description="The NOAEL (healthy volunteers) or STD10 / HNSTD (oncology), mg/kg/day.")
    endpoint: Literal["NOAEL", "STD10", "HNSTD"] = "NOAEL"
    body_weight_kg: float | None = Field(None, gt=0, description="Mean animal weight. Needed only for species not in FDA Table 1.")
    study: str | None = Field(None, description="Study ID / reference, carried into the result.")


class SpeciesHED(BaseModel):
    species: str
    study: str | None
    endpoint: str
    animal_dose_mg_per_kg: float
    conversion: str
    hed_mg_per_kg: float
    start_dose_mg_per_kg: float | None = Field(None, description="Oncology only: HED/10 (STD10) or HED/6 (HNSTD).")


class FIHDoseResult(GxPResult):
    approach: str
    per_species: list[SpeciesHED]
    selected_species: str
    selection_basis: str
    safety_factor: float | None
    mrsd_mg_per_kg: float
    mrsd_mg: float = Field(description="mrsd_mg_per_kg × human_weight_kg.")
    human_weight_kg: float


def _species(s: str) -> str:
    k = s.strip().lower()
    return ALIASES.get(k) or ALIASES.get(k.replace("-", "_"), k.replace("-", "_").replace(" ", "_"))


def first_in_human_dose(
    studies: list[ToxStudy],
    population: Literal["healthy_volunteers", "oncology"] = "healthy_volunteers",
    safety_factor: float = 10.0,
    safety_factor_justification: str | None = None,
    scaling: Literal["body_surface_area", "mg_per_kg"] = "body_surface_area",
    scaling_justification: str | None = None,
    most_appropriate_species: str | None = None,
    species_justification: str | None = None,
    human_weight_kg: float = 60.0,
    high_risk_biologic: bool = False,
) -> FIHDoseResult:
    if not studies:
        raise GxPInputError("Give at least one toxicology study (species and dose).")
    findings: list[Finding] = []
    assumptions: list[str] = []
    cites = []
    rows: list[SpeciesHED] = []
    raw: dict[int, tuple[float, float | None]] = {}  # full-precision (hed, start) per row

    if scaling == "mg_per_kg" and not scaling_justification:
        raise GxPInputError("mg/kg scaling needs scaling_justification (FDA 2005 section V.B lists the conditions); "
                            "otherwise use body_surface_area, which gives the safer MRSD.")
    for st in studies:
        sp = _species(st.species)
        if population == "healthy_volunteers" and st.endpoint != "NOAEL":
            raise GxPInputError(f"{st.species}: healthy-volunteer MRSD starts from the NOAEL, got {st.endpoint}. "
                                "For anticancer drugs use population='oncology'.")
        if population == "oncology":
            want = "STD10" if sp in RODENTS else "HNSTD"
            if st.endpoint != want:
                raise GxPInputError(f"{st.species}: ICH S9 uses the {want} for {'rodents' if sp in RODENTS else 'non-rodents'}, "
                                    f"got {st.endpoint}.")
        if scaling == "mg_per_kg":
            hed, conv = st.dose_mg_per_kg, "mg/kg scaling (HED = animal dose), justified: " + scaling_justification
        elif sp in TABLE1 and st.body_weight_kg is None:
            km, div = TABLE1[sp]
            hed, conv = st.dose_mg_per_kg / div, f"÷ {div} (FDA 2005 Table 1, km {km:g})"
        elif st.body_weight_kg is not None:
            f = (st.body_weight_kg / human_weight_kg) ** 0.33
            hed = st.dose_mg_per_kg * f
            conv = f"× ({st.body_weight_kg:g} kg / {human_weight_kg:g} kg)^0.33 = × {f:.3f} (FDA 2005 Table 1 footnote a)"
            if sp in TABLE1:
                findings.append(Finding(level="info", code="WEIGHT_FORMULA_USED",
                                        message=f"{st.species}: body weight given, so the 0.33-exponent formula was used instead "
                                                f"of the standard divisor {TABLE1[sp][1]} (gives {sig(st.dose_mg_per_kg / TABLE1[sp][1], 3)} mg/kg). "
                                                "FDA recommends the standard factors within the usual weight ranges (Appendix C)."))
        else:
            raise GxPInputError(f"Species '{st.species}' is not in FDA 2005 Table 1 "
                                f"({', '.join(sorted(TABLE1))}); give body_weight_kg to use the 0.33-exponent formula.")
        start = None
        if population == "oncology":
            start = hed / (10 if st.endpoint == "STD10" else 6)
        raw[len(rows)] = (hed, start)
        rows.append(SpeciesHED(species=sp, study=st.study, endpoint=st.endpoint, animal_dose_mg_per_kg=st.dose_mg_per_kg,
                               conversion=conv, hed_mg_per_kg=sig(hed), start_dose_mg_per_kg=sig(start) if start else None))

    idx = {id(r): i for i, r in enumerate(rows)}

    def key(r: SpeciesHED) -> float:
        h, st = raw[idx[id(r)]]
        return st if population == "oncology" else h
    lowest = min(rows, key=key)
    if most_appropriate_species:
        sp = _species(most_appropriate_species)
        chosen = [r for r in rows if r.species == sp]
        if not chosen:
            raise GxPInputError(f"most_appropriate_species '{most_appropriate_species}' has no study in the input.")
        if not species_justification:
            raise GxPInputError("Choosing a species other than the default needs species_justification "
                                "(e.g. ADME similarity, class experience, target expression).")
        sel = min(chosen, key=key)
        basis = f"Most appropriate species chosen by the user: {species_justification}"
        if key(sel) > key(lowest):
            findings.append(Finding(level="warning", code="NOT_MOST_SENSITIVE",
                                    message=f"Selected {sel.species} gives a higher dose than the most sensitive species "
                                            f"({lowest.species}); the justification must support this."))
    else:
        sel = min(rows, key=key)
        basis = ("Default: most sensitive species (lowest HED), FDA 2005 section VI" if population == "healthy_volunteers"
                 else "Lowest resulting start dose across species (conservative; ICH S9 uses the rodent STD10 unless the "
                      "non-rodent is the most appropriate species)")

    if population == "healthy_volunteers":
        if safety_factor < 10 and not safety_factor_justification:
            raise GxPInputError("A safety factor below 10 needs safety_factor_justification (FDA 2005 section VII.C).")
        mrsd = raw[idx[id(sel)]][0] / safety_factor
        sf = safety_factor
        if safety_factor != 10:
            findings.append(Finding(level="info", code="NON_DEFAULT_SAFETY_FACTOR",
                                    message=f"Safety factor {safety_factor:g} instead of the default 10"
                                            + (f": {safety_factor_justification}" if safety_factor_justification else ".")))
        cites += [cite("FDA MRSD guidance (2005)", "Table 1; section V (HED)", "Convert NOAEL to HED by body-surface-area factors.", FDA_MRSD_URL),
                  cite("FDA MRSD guidance (2005)", "Sections VI-VII", "Most sensitive species by default; MRSD = HED / safety factor (≥10 default).", FDA_MRSD_URL)]
        summary = (f"MRSD {sig(mrsd, 3)} mg/kg ({sig(mrsd * human_weight_kg, 3)} mg for {human_weight_kg:g} kg) from the "
                   f"{sel.species} NOAEL {sel.animal_dose_mg_per_kg:g} mg/kg (HED {sel.hed_mg_per_kg} mg/kg) ÷ {safety_factor:g}.")
    else:
        mrsd, sf = raw[idx[id(sel)]][1], None
        cites += [cite("ICH S9", "Note 2", "Start at 1/10 STD10 (rodent) or 1/6 HNSTD (non-rodent if most appropriate).", S9_URL),
                  cite("FDA MRSD guidance (2005)", "Table 1", "Body-surface-area conversion to HED.", FDA_MRSD_URL)]
        summary = (f"Oncology start dose {sig(mrsd, 3)} mg/kg ({sig(mrsd * human_weight_kg, 3)} mg for {human_weight_kg:g} kg): "
                   f"{'1/10 STD10' if sel.endpoint == 'STD10' else '1/6 HNSTD'} in {sel.species} "
                   f"(HED {sel.hed_mg_per_kg} mg/kg).")
        assumptions.append("ICH S9 applies to patients with advanced cancer; trials in healthy volunteers follow ICH M3(R2).")

    if high_risk_biologic:
        findings.append(Finding(level="action_required", code="MABEL_NEEDED",
                                message="High-risk biologic (e.g. agonist, immune activator): a NOAEL-based dose alone is not "
                                        "sufficient. Derive a MABEL / pharmacologically active dose and take the lower "
                                        "(EMA first-in-human guideline 2017). Not computed by this tool."))
    assumptions += [f"Human body weight {human_weight_kg:g} kg (FDA Table 1 assumes 60 kg).",
                    "Doses are mg/kg/day of the same route and regimen as intended clinically; adjust otherwise.",
                    "The MRSD is an upper limit; the actual starting dose may be lower (e.g. based on the PAD)."]
    inputs = {k: v for k, v in locals().items() if k in (
        "population", "safety_factor", "scaling", "most_appropriate_species", "human_weight_kg", "high_risk_biologic")}
    inputs["studies"] = [s.model_dump() for s in studies]
    return FIHDoseResult(
        summary=summary, findings=findings, assumptions=assumptions, citations=cites,
        provenance=provenance("ind_first_in_human_dose", "FDA MRSD 2005; ICH S9", inputs,
                              ["FDA 2005 Table 1 and Appendix C rabbit example"]),
        approach="FDA 2005 MRSD" if population == "healthy_volunteers" else "ICH S9",
        per_species=rows, selected_species=sel.species, selection_basis=basis, safety_factor=sf,
        mrsd_mg_per_kg=sig(mrsd), mrsd_mg=sig(mrsd * human_weight_kg), human_weight_kg=human_weight_kg)
