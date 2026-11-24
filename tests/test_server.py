"""End-to-end through the MCP protocol (in-memory client): schemas, annotations, structured output,
errors and resources as an agent actually sees them."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from opengxp.server import mcp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_tools_listed_with_schemas_and_annotations():
    async with Client(mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) >= {"cmc_impurity_thresholds", "cmc_residual_solvent_limit", "cmc_elemental_impurity_risk"}
    for t in tools.values():
        if t.name.endswith("_core_status"):
            continue  # setup probe, not a guideline result
        if t.name.startswith("ref_"):  # online reference-data lookups: read-only, but open-world (tested in tests/ref)
            assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is True
            assert t.output_schema and "dataset" in json.dumps(t.output_schema) and t.description and t.title
            continue
        assert t.output_schema and "citations" in json.dumps(t.output_schema)
        assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False
        assert t.description and t.title
    props = tools["cmc_impurity_thresholds"].input_schema["properties"]
    assert props["dose_unit"]["enum"] == ["mg", "g"]


async def test_structured_output_via_protocol():
    async with Client(mcp) as c:
        r = await c.call_tool("cmc_impurity_thresholds", {
            "product_type": "drug_substance", "max_daily_dose": 0.8, "dose_unit": "g",
            "impurities": [{"name": "Imp-A", "level_percent": 0.143}]})
    sc = r.structured_content
    assert sc["impurities"][0]["next_action"] == "identify_and_qualify"
    assert sc["citations"][0]["guideline"] == "ICH Q3A(R2)"
    assert len(sc["provenance"]["inputs_hash"]) == 64


async def test_actionable_error_reaches_client():
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="Did you mean"):
            await c.call_tool("cmc_residual_solvent_limit", {"solvents": ["dichloromethan"]})


async def test_resources():
    async with Client(mcp) as c:
        res = await c.read_resource("ich://q3c/solvents/DCM")
        assert json.loads(res[0].text)["cas"] == "75-09-2"
        el = json.loads((await c.read_resource("ich://q3d/elements/palladium"))[0].text)
        assert el["pde_ug_per_day"]["parenteral"] == 10
        prompts = await c.list_prompts()
        assert any(p.name == "impurity_assessment_report" for p in prompts)


async def test_stability_tools_via_protocol():
    csv = "batch,time_months,value\n" + "\n".join(
        f"{b},{t},{100 + d - 0.25 * t + e}" for b, d in [("A", 0), ("B", 0.2), ("C", -0.1)]
        for t, e in zip([0, 3, 6, 9, 12], [0.1, -0.1, 0.05, -0.05, 0.0]))
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert {"stab_estimate_shelf_life", "stab_extrapolation_limit", "stab_detect_oot", "stab_storage_conditions"} <= names
        r = await c.call_tool("stab_estimate_shelf_life", {
            "attribute": {"name": "Assay", "direction": "decreasing", "lower_spec": 95},
            "csv_text": csv, "storage": "room_temperature", "accelerated_significant_change": "none"})
        sc = r.structured_content
        assert sc["poolability"]["model"] in ("pooled", "common_slope", "separate")
        assert sc["supported_shelf_life_months"] <= 24
        e = await c.call_tool("stab_extrapolation_limit", {"storage": "refrigerated", "long_term_months": 12})
        assert e.structured_content["max_shelf_life_months"] == 18
        with pytest.raises(ToolError, match="csv_text"):
            await c.call_tool("stab_estimate_shelf_life", {"attribute": {"name": "A", "direction": "decreasing", "lower_spec": 95}})


async def test_ind_tools_via_protocol():
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert {"ind_ddi_risk", "ind_first_in_human_dose", "ind_safety_margins", "ind_tox_duration",
                "ind_metabolite_coverage"} <= names
        r = await c.call_tool("ind_first_in_human_dose", {"studies": [
            {"species": "rat", "dose_mg_per_kg": 50}, {"species": "dog", "dose_mg_per_kg": 10}]})
        assert r.structured_content["selected_species"] == "dog"
        d = await c.call_tool("ind_ddi_risk", {
            "perpetrator": {"cmax_total_uM": 2, "fu_p": 0.05, "dose_mg": 100, "mw_g_per_mol": 400, "blood_to_plasma": 1},
            "enzymes": [{"enzyme": "CYP3A", "ki_u_uM": 20}]})
        assert d.structured_content["conclusions"][0]["risk_excluded"] is False
        kdeg = json.loads((await c.read_resource("ich://m12/kdeg"))[0].text)
        assert kdeg["hepatic"]["CYP2C9"] == 0.00011
        with pytest.raises(ToolError, match="below 10"):
            await c.call_tool("ind_first_in_human_dose", {"studies": [{"species": "dog", "dose_mg_per_kg": 1}],
                                                         "safety_factor": 3})
