"""MCP server: CMC impurities (ICH Q3A/Q3B/Q3C/Q3D). Offline, deterministic, read-only."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from opengxp.cmc.elemental_impurities import (
    CTCL_UG_PER_G, PDE, Component, ElementalImpurityResult, assess_elemental_impurities,
    in_default_risk_assessment, normalise_element,
)
from opengxp.cmc.impurity_thresholds import ImpurityObservation, ImpurityThresholdResult, assess_impurity_thresholds
from opengxp.cmc.residual_solvents import (
    ComponentSolvent, ResidualSolventResult, assess_residual_solvents, resolve_solvent, solvent_table,
)
from opengxp.core import READ_ONLY_OFFLINE

cmc = FastMCP(
    "opengxp-cmc",
    instructions=(
        "Deterministic ICH calculators for drug-substance and drug-product impurities. Every result cites the "
        "guideline clause, lists assumptions and findings, and carries a provenance hash. Use "
        "cmc_impurity_thresholds for organic impurities/degradants (Q3A/Q3B), cmc_residual_solvent_limit for "
        "solvents (Q3C), cmc_elemental_impurity_risk for metals (Q3D). Browse reference data via the ich:// "
        "resources. Results are decision support, not regulatory advice."
    ),
    mask_error_details=True,
)

_DOSE = Annotated[float, Field(gt=0, description="Maximum daily dose of drug substance (Q3A/Q3B).")]


@cmc.tool(
    title="Impurity thresholds (ICH Q3A/Q3B)",
    tags={"cmc", "ich-q3a", "ich-q3b", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH Q3A(R2)", "ICH Q3B(R2)"], "validated_against": "Q3A/Q3B Attachment 2 examples"},
    version="1.0",
    timeout=30,
)
def cmc_impurity_thresholds(
    product_type: Annotated[Literal["drug_substance", "drug_product"],
                            Field(description="drug_substance -> ICH Q3A(R2); drug_product (degradation products) -> ICH Q3B(R2).")],
    max_daily_dose: _DOSE,
    dose_unit: Annotated[Literal["mg", "g"], Field(description="Unit of max_daily_dose. Never guessed.")],
    impurities: Annotated[list[ImpurityObservation] | None,
                          Field(max_length=200, description="Optional batch results to classify against the thresholds.")] = None,
    detail: Annotated[Literal["concise", "full"],
                      Field(description="'concise' drops impurities that need no action.")] = "full",
) -> ImpurityThresholdResult:
    """Reporting, identification and qualification thresholds for organic impurities at a given maximum daily
    dose, and which impurities cross them.

    Applies the guideline's own rounding convention (half-up; compare at the threshold's decimals; TDI from the
    reported result; 'exceeded' means strictly greater) and states whether the % or the TDI limb of
    'whichever is lower' applies. Use for: setting specifications, deciding whether an unknown peak must be
    identified or qualified. Not for mutagenic impurities (ICH M7), residual solvents or metals.
    """
    return assess_impurity_thresholds(product_type, max_daily_dose, dose_unit, impurities, detail)


@cmc.tool(
    title="Residual solvent limits (ICH Q3C)",
    tags={"cmc", "ich-q3c", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH Q3C(R9)"], "validated_against": "Q3C section 3.3 acetonitrile examples; Tables 1-4"},
    version="1.0",
    timeout=30,
)
def cmc_residual_solvent_limit(
    solvents: Annotated[list[str], Field(min_length=1, max_length=50,
                                         description="Solvent names, CAS numbers or synonyms, e.g. ['DCM', '67-56-1', 'THF'].")],
    option: Annotated[Literal["option1", "option2"],
                      Field(description="option1: fixed ppm limits assuming ≤10 g/day. option2: limits from the actual daily dose.")] = "option1",
    daily_dose_g: Annotated[float | None, Field(gt=0, description="Maximum daily mass of drug product, g. Required for option2.")] = None,
    measured_ppm: Annotated[dict[str, float] | None,
                            Field(description="Measured content per solvent, ppm, e.g. {'DCM': 450}. Checked against Option 1.")] = None,
    components: Annotated[list[ComponentSolvent] | None,
                          Field(max_length=50, description="Option 2 summation: each component's mass in the daily dose and its solvent content.")] = None,
) -> ResidualSolventResult:
    """Class, PDE and permitted concentration for residual solvents, with pass/fail for measured values and the
    Option 2 sum across drug-product components.

    Resolves names, CAS numbers and synonyms exactly; unknown names return 'did you mean' suggestions instead of
    a guess. Flags Class 1 solvents and Table 4 solvents with no PDE.
    """
    return assess_residual_solvents(solvents, option, daily_dose_g, measured_ppm, components)


@cmc.tool(
    title="Elemental impurity risk assessment (ICH Q3D)",
    tags={"cmc", "ich-q3d", "validated"},
    annotations=READ_ONLY_OFFLINE,
    meta={"guidelines": ["ICH Q3D(R2)"], "validated_against": "Q3D Appendix 4 example; Tables A.2.1/A.2.2/A.5.1"},
    version="1.0",
    timeout=30,
)
def cmc_elemental_impurity_risk(
    route: Annotated[Literal["oral", "parenteral", "inhalation", "cutaneous"],
                     Field(description="Route of administration; selects the PDE column (cutaneous = Q3D(R2) Appendix 5).")],
    daily_dose_g: Annotated[float, Field(gt=0, description="Maximum daily intake of drug product, g.")],
    option: Annotated[Literal["option1", "option2a", "option2b", "option3"],
                      Field(description="option1: common limits at 10 g/day; option2a: common limits at the actual dose; "
                                        "option2b: component-specific, summed against the PDE; option3: finished-product analysis.")] = "option2b",
    components: Annotated[list[Component] | None,
                          Field(max_length=50, description="Components with their mass in the daily dose and known element levels (µg/g).")] = None,
    intentionally_added: Annotated[list[str] | None,
                                   Field(description="Elements deliberately added, e.g. catalysts ['Pd', 'Ni']. Always assessed.")] = None,
    elements: Annotated[list[str] | None,
                        Field(description="Override the element list. Default: Q3D Table 5.1 for the route + intentionally added + measured.")] = None,
    control_threshold_fraction: Annotated[float, Field(gt=0, le=1, description="Control threshold as fraction of PDE (Q3D: 0.30).")] = 0.30,
) -> ElementalImpurityResult:
    """Component-approach risk assessment for elemental impurities: which elements to assess, their PDEs,
    permitted concentrations, total daily intake vs PDE, the 30% control threshold, and the components driving
    each element. Applies the cutaneous concentration limit (CTCL) for Ni and Co.
    """
    return assess_elemental_impurities(route, daily_dose_g, option, components, intentionally_added,
                                       elements, control_threshold_fraction)


# ---- Resources: reference data an agent can browse without spending a tool call on reasoning -------------
@cmc.resource("ich://q3c/solvents", mime_type="application/json", tags={"ich-q3c"},
              description="All ICH Q3C(R9) solvents: class, PDE, Option 1 limit, CAS, synonyms.")
def q3c_solvents() -> list[dict]:
    return [s.model_dump() for s in solvent_table()]


@cmc.resource("ich://q3c/solvents/{key}", mime_type="application/json", tags={"ich-q3c"},
              description="One Q3C solvent by name, CAS number or synonym.")
def q3c_solvent(key: str) -> dict:
    return resolve_solvent(key).model_dump()


@cmc.resource("ich://q3d/elements", mime_type="application/json", tags={"ich-q3d"},
              description="All ICH Q3D(R2) elements: class and PDE (µg/day) for every route.")
def q3d_elements() -> list[dict]:
    return [q3d_element(sym) for sym in PDE]


@cmc.resource("ich://q3d/elements/{symbol}", mime_type="application/json", tags={"ich-q3d"},
              description="One Q3D element: class, PDEs by route, CTCL, default risk-assessment inclusion.")
def q3d_element(symbol: str) -> dict:
    sym = normalise_element(symbol)
    name, cls, o, p, i, c = PDE[sym]
    return {"symbol": sym, "name": name, "class": cls,
            "pde_ug_per_day": {"oral": o, "parenteral": p, "inhalation": i, "cutaneous": c},
            "ctcl_ug_per_g": CTCL_UG_PER_G.get(sym),
            "in_default_risk_assessment": {r: in_default_risk_assessment(sym, r)
                                           for r in ("oral", "parenteral", "inhalation", "cutaneous")}}


@cmc.prompt(description="Draft a CTD 3.2.P.5.5-style impurity characterisation summary using the cmc tools.",
            tags={"cmc"})
def impurity_assessment_report(product_name: str, route: str, max_daily_dose: str) -> str:
    return (
        f"Prepare an impurity assessment for {product_name} ({route}, maximum daily dose {max_daily_dose}).\n"
        "1. Call cmc_impurity_thresholds for the drug substance and the drug product with the batch data provided.\n"
        "2. Call cmc_residual_solvent_limit for every solvent used in the last synthetic steps and in manufacture.\n"
        "3. Call cmc_elemental_impurity_risk with the components and any catalysts as intentionally_added.\n"
        "Write the summary as tables. Quote every number exactly as returned, cite the guideline section from each "
        "result's citations, list all action_required findings first, and end with the disclaimer."
    )
