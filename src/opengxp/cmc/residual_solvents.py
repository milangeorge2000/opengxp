"""ICH Q3C(R9) residual solvents: class, PDE, Option 1 / Option 2 limits, and Option 2 summation
across drug-product components (section 3.3)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

from opengxp.core import Citation, Finding, GxPInputError, GxPResult, provenance

Q3C_URL = ("https://www.ema.europa.eu/en/documents/scientific-guideline/"
           "ich-q3c-r9-guideline-impurities-guideline-residual-solvents-step-5_en.pdf")
SolventClass = Literal["1", "2", "3", "no_pde"]


class SolventRecord(BaseModel):
    name: str
    cas: str
    solvent_class: SolventClass
    pde_mg_per_day: float | None = Field(description="Class 2 PDE; for Class 3 the 50 mg/day default.")
    option1_limit_ppm: float | None
    synonyms: list[str] = []
    note: str | None = None


@lru_cache
def solvent_table() -> list[SolventRecord]:
    raw = json.loads(files("opengxp.cmc.data").joinpath("q3c_solvents.json").read_text())
    return [SolventRecord(**s) for s in raw["solvents"]]


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_,()]+", "", s.lower())


@lru_cache
def _index() -> dict[str, SolventRecord]:
    idx: dict[str, SolventRecord] = {}
    for s in solvent_table():
        for key in [s.name, s.cas, *s.synonyms]:
            idx[_norm(key)] = s
    return idx


def resolve_solvent(query: str) -> SolventRecord:
    """Exact name / CAS / synonym match, else a 'did you mean' error. Never silently guesses."""
    hit = _index().get(_norm(query))
    if hit:
        return hit
    choices = {s.name: s for s in solvent_table()} | {syn: s for s in solvent_table() for syn in s.synonyms}
    best = process.extract(query, list(choices), scorer=fuzz.WRatio, limit=3)
    sugg = "; ".join(dict.fromkeys(f"{choices[m].name} ({choices[m].cas})" for m, score, _ in best if score >= 60))
    raise GxPInputError(
        f"Unknown solvent '{query}'. " + (f"Did you mean: {sugg}? " if sugg else "")
        + "Pass the ICH name, CAS number or a common synonym; read resource ich://q3c/solvents for the full list. "
        "Solvents not in Q3C have no ICH limit and need a toxicological justification."
    )


class ComponentSolvent(BaseModel):
    name: str = Field(description="Component, e.g. 'drug substance', 'lactose'.")
    mass_g: float = Field(gt=0, description="Amount of this component in the maximum daily dose, g.")
    solvent_ppm: dict[str, float] = Field(description="Measured (or specification) solvent content, ppm, keyed by solvent.")


class SolventAssessment(BaseModel):
    query: str
    name: str
    cas: str
    solvent_class: SolventClass
    pde_mg_per_day: float | None
    option1_limit_ppm: float | None
    option2_limit_ppm: float | None = Field(None, description="1000 x PDE / daily dose (Option 2), if a dose was given.")
    measured_ppm: float | None = None
    passes_option1: bool | None = None
    option2_total_mg_per_day: float | None = Field(None, description="Sum over components (Option 2).")
    option2_product_ppm: float | None = None
    passes_option2: bool | None = None
    note: str | None = None


class ResidualSolventResult(GxPResult):
    option: Literal["option1", "option2"]
    daily_dose_g: float | None
    solvents: list[SolventAssessment]


def assess_residual_solvents(
    solvents: list[str],
    option: Literal["option1", "option2"] = "option1",
    daily_dose_g: float | None = None,
    measured_ppm: dict[str, float] | None = None,
    components: list[ComponentSolvent] | None = None,
) -> ResidualSolventResult:
    if not solvents:
        raise GxPInputError("Give at least one solvent (name, CAS or synonym).")
    if option == "option2" and not daily_dose_g:
        raise GxPInputError("Option 2 needs daily_dose_g (maximum daily mass of drug product, g). "
                            "Use option='option1' if the dose is unknown.")
    if components and not daily_dose_g:
        daily_dose_g = sum(c.mass_g for c in components)

    measured_ppm = measured_ppm or {}
    measured_by_cas = {resolve_solvent(k).cas: v for k, v in measured_ppm.items()}  # keys may be any synonym
    findings: list[Finding] = []
    assumptions: list[str] = []
    if option == "option1":
        assumptions.append("Option 1 assumes a daily dose of not more than 10 g of drug product.")
        if daily_dose_g and daily_dose_g > 10:
            findings.append(Finding(level="action_required", code="OPTION1_NOT_APPLICABLE",
                                    message=f"Daily dose {daily_dose_g} g exceeds 10 g: Option 1 limits do not apply; use Option 2."))

    # component masses by resolved solvent
    comp_by_solvent: dict[str, list[tuple[str, float, float]]] = {}
    for c in components or []:
        for q, ppm in c.solvent_ppm.items():
            comp_by_solvent.setdefault(resolve_solvent(q).cas, []).append((c.name, c.mass_g, ppm))

    out: list[SolventAssessment] = []
    for q in solvents:
        s = resolve_solvent(q)
        a = SolventAssessment(query=q, name=s.name, cas=s.cas, solvent_class=s.solvent_class,
                              pde_mg_per_day=s.pde_mg_per_day, option1_limit_ppm=s.option1_limit_ppm, note=s.note)
        if daily_dose_g and s.pde_mg_per_day is not None and s.solvent_class in ("2", "3"):
            a.option2_limit_ppm = round(1000 * s.pde_mg_per_day / daily_dose_g, 1)
        m = measured_by_cas.get(s.cas)
        if m is not None and s.option1_limit_ppm is not None:
            a.measured_ppm = m
            a.passes_option1 = m <= s.option1_limit_ppm
            if not a.passes_option1 and option == "option1":
                findings.append(Finding(level="action_required", code="ABOVE_OPTION1",
                                        message=f"{s.name}: {m} ppm exceeds the Option 1 limit of {s.option1_limit_ppm:g} ppm."))
        parts = comp_by_solvent.get(s.cas)
        if parts and s.pde_mg_per_day is not None and s.solvent_class in ("2", "3"):
            total_mg = sum(mass * ppm / 1000 for _, mass, ppm in parts)  # g x ppm / 1000 = mg
            total_mass = sum(mass for _, mass, _ in parts)
            a.option2_total_mg_per_day = round(total_mg, 4)
            a.option2_product_ppm = round(total_mg * 1000 / total_mass, 1)
            a.passes_option2 = total_mg <= s.pde_mg_per_day
            over = [n for n, _, ppm in parts if s.option1_limit_ppm and ppm > s.option1_limit_ppm]
            if over and a.passes_option2:
                findings.append(Finding(level="info", code="OPTION2_RESCUES",
                                        message=f"{s.name}: {', '.join(over)} exceed the Option 1 limit, but the product "
                                                f"meets Option 2 ({total_mg:.2f} mg/day ≤ PDE {s.pde_mg_per_day:g} mg/day)."))
            if not a.passes_option2:
                findings.append(Finding(level="action_required", code="ABOVE_PDE",
                                        message=f"{s.name}: {total_mg:.2f} mg/day exceeds the PDE of {s.pde_mg_per_day:g} mg/day."))
        if s.solvent_class == "1":
            findings.append(Finding(level="warning", code="CLASS1_SOLVENT",
                                    message=f"{s.name} is a Class 1 solvent: avoid; use only if strongly justified "
                                            f"(limit {s.option1_limit_ppm:g} ppm)."))
        elif s.solvent_class == "no_pde":
            findings.append(Finding(level="warning", code="NO_PDE",
                                    message=f"{s.name}: Q3C found no adequate toxicological data; residual levels must be justified."))
        out.append(a)

    summary = "; ".join(
        f"{a.name}: Class {a.solvent_class}" + (f", PDE {a.pde_mg_per_day:g} mg/day" if a.pde_mg_per_day else "")
        + (f", Option 1 limit {a.option1_limit_ppm:g} ppm" if a.option1_limit_ppm else "")
        + (f", Option 2 limit {a.option2_limit_ppm:g} ppm" if a.option2_limit_ppm else "")
        for a in out) + "."
    return ResidualSolventResult(
        summary=summary.replace("Class no_pde", "no PDE (Table 4)"),
        findings=findings,
        assumptions=assumptions,
        citations=[
            Citation(guideline="ICH Q3C(R9)", section="Tables 1-4", rule="Solvent class, PDE and Option 1 concentration limit.", url=Q3C_URL),
            Citation(guideline="ICH Q3C(R9)", section="3.3 Options for describing limits of Class 2 solvents",
                     rule="Concentration (ppm) = 1000 x PDE / dose; Option 2 sums solvent amounts over components.", url=Q3C_URL),
        ],
        provenance=provenance("cmc_residual_solvent_limit", "Q3C(R9)",
                              dict(solvents=solvents, option=option, dose=daily_dose_g, measured=measured_ppm,
                                   components=[c.model_dump() for c in components or []]),
                              ["ICH Q3C(R9) 3.3 acetonitrile examples 1-2", "Tables 1-4 transcription"]),
        option=option,
        daily_dose_g=daily_dose_g,
        solvents=out,
    )
