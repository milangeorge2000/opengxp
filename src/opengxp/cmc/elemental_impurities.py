"""ICH Q3D(R2) elemental impurities: PDEs by route (Table A.2.1, Appendix 5 Table A.5.1),
risk-assessment element list (Table 5.1 / A.5.1), Options 1 / 2a / 2b / 3 (section 7),
the 30% control threshold (section 5.6) and the cutaneous CTCL for Ni and Co."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import Citation, Finding, GxPInputError, GxPResult, provenance

Q3D_URL = "https://database.ich.org/sites/default/files/Q3D-R2_Guideline_Step4_2022_0308.pdf"
Route = Literal["oral", "parenteral", "inhalation", "cutaneous"]
Option = Literal["option1", "option2a", "option2b", "option3"]

# symbol: (name, class, oral, parenteral, inhalation, cutaneous) PDE µg/day.
# Oral/parenteral/inhalation: Table A.2.1. Cutaneous: Table A.5.1 (Pd PDE applies to Ir, Os, Rh, Ru: footnote 5).
PDE: dict[str, tuple[str, str, float, float, float, float]] = {
    "Cd": ("cadmium", "1", 5, 2, 3, 20),
    "Pb": ("lead", "1", 5, 5, 5, 50),
    "As": ("arsenic", "1", 15, 15, 2, 30),
    "Hg": ("mercury", "1", 30, 3, 1, 30),
    "Co": ("cobalt", "2A", 50, 5, 3, 50),
    "V": ("vanadium", "2A", 100, 10, 1, 100),
    "Ni": ("nickel", "2A", 200, 20, 6, 200),
    "Tl": ("thallium", "2B", 8, 8, 8, 8),
    "Au": ("gold", "2B", 300, 300, 3, 3000),
    "Pd": ("palladium", "2B", 100, 10, 1, 100),
    "Ir": ("iridium", "2B", 100, 10, 1, 100),
    "Os": ("osmium", "2B", 100, 10, 1, 100),
    "Rh": ("rhodium", "2B", 100, 10, 1, 100),
    "Ru": ("ruthenium", "2B", 100, 10, 1, 100),
    "Se": ("selenium", "2B", 150, 80, 130, 800),
    "Ag": ("silver", "2B", 150, 15, 7, 150),
    "Pt": ("platinum", "2B", 100, 10, 1, 100),
    "Li": ("lithium", "3", 550, 250, 25, 2500),
    "Sb": ("antimony", "3", 1200, 90, 20, 900),
    "Ba": ("barium", "3", 1400, 700, 300, 7000),
    "Mo": ("molybdenum", "3", 3000, 1500, 10, 15000),
    "Cu": ("copper", "3", 3000, 300, 30, 3000),
    "Sn": ("tin", "3", 6000, 600, 60, 6000),
    "Cr": ("chromium", "3", 11000, 1100, 3, 11000),
}
_ROUTE_COL = {"oral": 2, "parenteral": 3, "inhalation": 4, "cutaneous": 5}
CTCL_UG_PER_G = {"Co": 35.0, "Ni": 35.0}  # cutaneous & transcutaneous concentration limit (sensitisers)

# Include in risk assessment if NOT intentionally added (Table 5.1; cutaneous from Table A.5.1).
_CLASS3_RA = {"Li": {"parenteral", "inhalation"}, "Sb": {"parenteral", "inhalation"},
              "Cu": {"parenteral", "inhalation"}, "Ba": {"inhalation"}, "Mo": {"inhalation"},
              "Sn": {"inhalation"}, "Cr": {"inhalation"}}
NO_PDE_ELEMENTS = {"Al", "B", "Ca", "Fe", "K", "Mg", "Mn", "Na", "W", "Zn"}
_NAMES = {v[0]: k for k, v in PDE.items()} | {"aluminium": "Al", "aluminum": "Al", "boron": "B", "calcium": "Ca",
         "iron": "Fe", "potassium": "K", "magnesium": "Mg", "manganese": "Mn", "sodium": "Na",
         "tungsten": "W", "zinc": "Zn"}


def normalise_element(q: str) -> str:
    s = q.strip()
    for sym in list(PDE) + sorted(NO_PDE_ELEMENTS):
        if s.lower() == sym.lower():
            return sym
    if s.lower() in _NAMES:
        return _NAMES[s.lower()]
    raise GxPInputError(f"Unknown element '{q}'. Use a symbol (e.g. 'Pd') or English name (e.g. 'palladium'). "
                        f"Q3D elements: {', '.join(PDE)}.")


def pde(symbol: str, route: Route) -> float:
    return float(PDE[symbol][_ROUTE_COL[route]])


def in_default_risk_assessment(symbol: str, route: Route) -> bool:
    cls = PDE[symbol][1]
    if cls in ("1", "2A"):
        return True
    if cls == "2B" or route == "cutaneous":
        return False
    return route in _CLASS3_RA.get(symbol, set())


class Component(BaseModel):
    name: str = Field(description="Drug substance, excipient, or 'drug product' (Option 3).")
    mass_g: float = Field(gt=0, description="Amount of this component in the maximum daily intake, g.")
    element_ug_per_g: dict[str, float] = Field(
        default_factory=dict,
        description="Known/measured element concentrations, µg/g, keyed by symbol or name. "
                    "For results < LoQ pass the LoQ for a conservative estimate, or omit the element.")


class Driver(BaseModel):
    component: str
    intake_ug: float
    share: float = Field(description="Fraction of total intake of this element.")


class ElementAssessment(BaseModel):
    symbol: str
    element_class: str
    pde_ug_per_day: float
    included_because: list[str] = Field(description="Why it is in the assessment: 'Table 5.1', 'intentionally added', 'measured', 'requested'.")
    permitted_conc_ug_per_g: float | None = Field(None, description="Common permitted concentration (Option 1 / 2a).")
    ctcl_ug_per_g: float | None = None
    total_intake_ug: float | None = None
    percent_of_pde: float | None = None
    above_control_threshold: bool | None = None
    exceeds_pde: bool | None = None
    components_over_limit: list[str] = []
    drivers: list[Driver] = []


class ElementalImpurityResult(GxPResult):
    route: Route
    option: Option
    daily_dose_g: float
    elements: list[ElementAssessment]
    elements_without_pde: list[str] = []


def assess_elemental_impurities(
    route: Route,
    daily_dose_g: float,
    option: Option = "option2b",
    components: list[Component] | None = None,
    intentionally_added: list[str] | None = None,
    elements: list[str] | None = None,
    control_threshold_fraction: float = 0.30,
) -> ElementalImpurityResult:
    if daily_dose_g <= 0:
        raise GxPInputError("daily_dose_g must be > 0 (maximum daily intake of drug product, g).")
    components = components or []
    findings: list[Finding] = []
    assumptions: list[str] = []

    why: dict[str, list[str]] = {}
    if elements:
        for e in elements:
            why.setdefault(normalise_element(e), []).append("requested")
    else:
        for sym in PDE:
            if in_default_risk_assessment(sym, route):
                why.setdefault(sym, []).append("Table 5.1" if route != "cutaneous" else "Table A.5.1")
    for e in intentionally_added or []:
        why.setdefault(normalise_element(e), []).append("intentionally added")

    conc: dict[str, dict[str, float]] = {}  # symbol -> component -> µg/g
    for c in components:
        for e, v in c.element_ug_per_g.items():
            sym = normalise_element(e)
            conc.setdefault(sym, {})[c.name] = v
            if "measured" not in why.setdefault(sym, []):
                why[sym].append("measured")

    no_pde = sorted(s for s in why if s in NO_PDE_ELEMENTS)
    for s in no_pde:
        findings.append(Finding(level="info", code="NO_Q3D_PDE",
                                message=f"{s}: no PDE in Q3D (low toxicity / regional rules); assess under other guidance."))

    if option == "option1" and daily_dose_g > 10:
        findings.append(Finding(level="action_required", code="OPTION1_NOT_APPLICABLE",
                                message=f"Option 1 assumes ≤ 10 g/day; daily dose is {daily_dose_g} g. Use Option 2a, 2b or 3."))
    if option == "option3" and len(components) > 1:
        assumptions.append("Option 3: components are summed as if they were the finished product.")

    out: list[ElementAssessment] = []
    for sym, reasons in why.items():
        if sym not in PDE:
            continue
        p = pde(sym, route)
        a = ElementAssessment(symbol=sym, element_class=PDE[sym][1], pde_ug_per_day=p, included_because=reasons)
        if route == "cutaneous" and sym in CTCL_UG_PER_G:
            a.ctcl_ug_per_g = CTCL_UG_PER_G[sym]
        if option in ("option1", "option2a"):
            dose = 10.0 if option == "option1" else daily_dose_g
            lim = p / dose
            if a.ctcl_ug_per_g is not None:
                lim = min(lim, a.ctcl_ug_per_g)
            a.permitted_conc_ug_per_g = round(lim, 4)
            a.components_over_limit = [n for n, v in conc.get(sym, {}).items() if v > lim]
        data = conc.get(sym)
        if data:
            masses = {c.name: c.mass_g for c in components}
            parts = {n: masses[n] * v for n, v in data.items()}
            total = sum(parts.values())
            a.total_intake_ug = round(total, 4)
            a.percent_of_pde = round(100 * total / p, 2)
            a.above_control_threshold = total >= control_threshold_fraction * p
            a.exceeds_pde = total > p
            a.drivers = sorted((Driver(component=n, intake_ug=round(v, 4), share=round(v / total, 4) if total else 0.0)
                                for n, v in parts.items()), key=lambda d: -d.intake_ug)
            if a.ctcl_ug_per_g is not None and total / daily_dose_g > a.ctcl_ug_per_g:
                findings.append(Finding(level="action_required", code="ABOVE_CTCL",
                                        message=f"{sym}: {total / daily_dose_g:.1f} µg/g in product exceeds the CTCL of "
                                                f"{a.ctcl_ug_per_g:g} µg/g (skin sensitiser)."))
            if a.exceeds_pde:
                findings.append(Finding(level="action_required", code="ABOVE_PDE",
                                        message=f"{sym}: {total:.2f} µg/day exceeds the {route} PDE of {p:g} µg/day "
                                                f"(main source: {a.drivers[0].component})."))
            elif a.above_control_threshold:
                findings.append(Finding(level="warning", code="ABOVE_CONTROL_THRESHOLD",
                                        message=f"{sym}: {a.percent_of_pde:.0f}% of PDE (≥ {control_threshold_fraction:.0%} "
                                                f"control threshold): additional controls needed."))
        if a.components_over_limit and not a.exceeds_pde:
            findings.append(Finding(level="warning", code="COMPONENT_OVER_COMMON_LIMIT",
                                    message=f"{sym}: {', '.join(a.components_over_limit)} exceed the common "
                                            f"{option} limit of {a.permitted_conc_ug_per_g:g} µg/g; consider Option 2b."))
        out.append(a)

    n_act = sum(f.level == "action_required" for f in findings)
    summary = (f"Q3D {route} risk assessment, {daily_dose_g:g} g/day, {option}: {len(out)} elements assessed; "
               f"{n_act} need action." + (" Elements over 30% of PDE: " +
               ", ".join(a.symbol for a in out if a.above_control_threshold) + "." if any(a.above_control_threshold for a in out) else ""))
    return ElementalImpurityResult(
        summary=summary, findings=findings, assumptions=assumptions,
        citations=[
            Citation(guideline="ICH Q3D(R2)", section="Appendix 2, Table A.2.1" + ("; Appendix 5, Table A.5.1" if route == "cutaneous" else ""),
                     rule="Permitted daily exposures by route.", url=Q3D_URL),
            Citation(guideline="ICH Q3D(R2)", section="Table 5.1", rule="Elements to include in the risk assessment.", url=Q3D_URL),
            Citation(guideline="ICH Q3D(R2)", section="Section 7 (Options 1, 2a, 2b, 3), Equations 1-2",
                     rule="Concentration (µg/g) = PDE / daily intake; intake = Σ component mass x concentration.", url=Q3D_URL),
            Citation(guideline="ICH Q3D(R2)", section="Section 5.6 Control of elemental impurities",
                     rule="Control threshold = 30% of PDE.", url=Q3D_URL),
        ],
        provenance=provenance("cmc_elemental_impurity_risk", "Q3D(R2)",
                              dict(route=route, dose=daily_dose_g, option=option,
                                   components=[c.model_dump() for c in components],
                                   added=intentionally_added, elements=elements, ct=control_threshold_fraction),
                              ["ICH Q3D(R2) Appendix 4 examples (Options 1, 2a, 2b)", "Tables A.2.1/A.2.2/A.5.1 transcription"]),
        route=route, option=option, daily_dose_g=daily_dose_g, elements=out, elements_without_pde=no_pde,
    )
