"""MCP server: public FDA reference data (phase 1: Inactive Ingredient Database). Read-only lookups against
the OpenGxP reference database (Supabase), made with the caller's own token so row-level security applies."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from opengxp.core.errors import GxPInputError
from opengxp.ref.client import RefAccessError, RefClient
from opengxp.ref.excipients import (
    DatasetInfo,
    ExcipientCheckResult,
    ExcipientInput,
    build_result,
)

READ_ONLY_REMOTE = {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False, "openWorldHint": True}

ref = FastMCP(
    "opengxp-ref",
    instructions=(
        "Exact, versioned lookups of public FDA reference data. Phase 1: the Inactive Ingredient Database (IID). "
        "Workflow: ref_resolve_ingredient and ref_resolve_dosage_form turn the user's words into FDA's exact names "
        "(ask the user when several candidates come back), then ref_check_excipients compares a formula with FDA "
        "precedent, and ref_excipient_precedents lists every approved use of one excipient. "
        "Access is role-based: if a call is denied, tell the user to ask their administrator for a role. "
        "Every result names the FDA release it came from. Decision support, not regulatory advice."
    ),
    mask_error_details=True,
)

_client_factory = RefClient   # replaced in tests


def _client() -> RefClient:
    return _client_factory()


class IngredientCandidate(BaseModel):
    ingredient_name: str
    unii: str | None = None
    cas_number: str | None = None
    matched_on: Literal["exact_name", "unii", "cas", "prefix", "similar"]
    score: float
    entries: int = Field(description="Number of IID rows for this ingredient.")


class DosageFormCandidate(BaseModel):
    dosage_form: str
    matched_on: Literal["exact", "prefix", "similar"]
    score: float
    entries: int


class ResolveIngredientResult(BaseModel):
    query: str
    candidates: list[IngredientCandidate]
    exact: bool = Field(description="True when exactly one candidate matches by name, UNII or CAS.")
    advice: str
    dataset: DatasetInfo


class ResolveDosageFormResult(BaseModel):
    query: str
    route: str | None
    candidates: list[DosageFormCandidate]
    exact: bool
    advice: str
    dataset: DatasetInfo


class PrecedentRow(BaseModel):
    row_no: int
    route: str
    dosage_form: str
    potency_amount: float | None = None
    potency_unit: str | None = None
    max_daily_exposure: float | None = None
    max_daily_exposure_unit: str | None = None
    record_updated: bool = False


class PrecedentsResult(BaseModel):
    ingredient: str
    unii: str | None
    cas_number: str | None
    rows: list[PrecedentRow]
    truncated: bool
    summary: str
    dataset: DatasetInfo


class VocabResult(BaseModel):
    kind: str
    values: list[dict]
    dataset: DatasetInfo


class DatasetStatusResult(BaseModel):
    datasets: list[dict]
    note: str


async def _iid_dataset(c: RefClient) -> DatasetInfo:
    rows = [r for r in await c.rpc("dataset_status") if r.get("dataset") == "iid"]
    if not rows:
        raise RefAccessError("No readable IID release: your account has no role that grants the 'iid' dataset "
                             "(ask an administrator), or no IID release is published yet.")
    r = rows[0]
    return DatasetInfo(label=r["label"], source_url=r["source_url"], source_sha256=r["source_sha256"],
                       row_count=r["row_count"], published_at=r.get("published_at"))


@ref.tool(title="Resolve an excipient name to FDA's IID name", tags={"ref", "iid"}, annotations=READ_ONLY_REMOTE,
          version="1.0", timeout=30)
async def ref_resolve_ingredient(
    query: Annotated[str, Field(min_length=2, max_length=200, description="Name, UNII or CAS number as the user wrote it.")],
    max_results: Annotated[int, Field(ge=1, le=50)] = 10,
) -> ResolveIngredientResult:
    """Find the exact FDA Inactive Ingredient Database name for an excipient. Ranks exact name, then UNII or CAS,
    then prefix, then similar spellings. Similar spellings are suggestions only: when more than one candidate
    comes back, ask the user which one they mean. FDA naming is literal (e.g. '.ALPHA.-TOCOPHEROL'); common
    abbreviations like 'PEG 400' may not resolve yet."""
    c = _client()
    ds = await _iid_dataset(c)
    cands = [IngredientCandidate(**r) for r in await c.rpc("resolve_ingredient", {"q": query, "max_results": max_results})]
    strong = [x for x in cands if x.matched_on in ("exact_name", "unii", "cas")]
    exact = len({x.ingredient_name for x in strong}) == 1
    advice = (f"Use '{strong[0].ingredient_name}'." if exact else
              "No match. Check the spelling, or try the UNII or CAS number." if not cands else
              "Several candidates: ask the user which one they mean before checking limits.")
    return ResolveIngredientResult(query=query, candidates=cands, exact=exact, advice=advice, dataset=ds)


@ref.tool(title="Resolve a dosage form to FDA's IID term", tags={"ref", "iid"}, annotations=READ_ONLY_REMOTE,
          version="1.0", timeout=30)
async def ref_resolve_dosage_form(
    query: Annotated[str, Field(min_length=2, max_length=120, description="Dosage form as the user wrote it, e.g. 'film coated tablet'.")],
    route: Annotated[str | None, Field(max_length=60, description="FDA route, e.g. ORAL, to limit candidates to forms used by that route.")] = None,
    max_results: Annotated[int, Field(ge=1, le=50)] = 10,
) -> ResolveDosageFormResult:
    """Find the exact FDA dosage-form term (e.g. 'TABLET, FILM COATED'). Different forms have separate IID limits,
    so confirm the form with the user when it is not an exact match."""
    c = _client()
    ds = await _iid_dataset(c)
    cands = [DosageFormCandidate(**r) for r in
             await c.rpc("resolve_dosage_form", {"q": query, "route": route, "max_results": max_results})]
    exact = any(x.matched_on == "exact" for x in cands)
    advice = (f"Use '{cands[0].dosage_form}'." if exact else
              "No match. Use ref_list_vocab(kind='dosage_form') to browse." if not cands else
              "No exact match: confirm the dosage form with the user.")
    return ResolveDosageFormResult(query=query, route=route, candidates=cands, exact=exact, advice=advice, dataset=ds)


@ref.tool(title="Check excipient levels against FDA precedent (IID)", tags={"ref", "iid", "formulation"},
          annotations=READ_ONLY_REMOTE, version="1.0", timeout=60,
          meta={"source": "FDA Inactive Ingredient Database"})
async def ref_check_excipients(
    route: Annotated[str, Field(min_length=2, max_length=60, description="FDA route, e.g. ORAL, TOPICAL, INTRAVENOUS.")],
    dosage_form: Annotated[str, Field(min_length=2, max_length=120, description="Exact FDA dosage form from ref_resolve_dosage_form.")],
    excipients: Annotated[list[ExcipientInput], Field(min_length=1, max_length=60)],
    units_per_day: Annotated[float | None, Field(gt=0, le=1000, description="Maximum dosage units per day; needed to compare "
                                                                          "with FDA's daily-exposure limits.")] = None,
) -> ExcipientCheckResult:
    """Compare each excipient amount with the highest level FDA has approved for the same route and dosage form
    (FDA Inactive Ingredient Database). Verdicts: within_precedent, above_precedent, not_comparable (FDA's unit
    differs), precedent_without_limit, no_precedent. Exact matching only: resolve names first. Above precedent is
    not a ban; it means a safety justification is needed."""
    c = _client()
    ds = await _iid_dataset(c)
    rows_by_item = [await c.rpc("iid_rows", {"ingredient": e.ingredient, "route": route, "dosage_form": dosage_form})
                    for e in excipients]
    return build_result(excipients, route.upper().strip(), dosage_form.upper().strip(), rows_by_item, units_per_day, ds)


@ref.tool(title="Every FDA-approved use of an excipient (IID)", tags={"ref", "iid", "formulation"},
          annotations=READ_ONLY_REMOTE, version="1.0", timeout=30)
async def ref_excipient_precedents(
    ingredient: Annotated[str, Field(min_length=2, max_length=200, description="Exact IID name from ref_resolve_ingredient.")],
    route: Annotated[str | None, Field(max_length=60, description="Optional FDA route filter, e.g. ORAL.")] = None,
) -> PrecedentsResult:
    """List every route and dosage form where FDA lists this excipient, with the maximum amounts. Use it to find
    alternatives when a level is above precedent."""
    c = _client()
    ds = await _iid_dataset(c)
    rows = await c.rpc("iid_rows", {"ingredient": ingredient, "route": route})
    if not rows:
        raise GxPInputError(f"No IID rows for '{ingredient}'{' by the ' + route + ' route' if route else ''}. "
                            "Resolve the exact name with ref_resolve_ingredient first.")
    out = [PrecedentRow(**{k: r.get(k) for k in PrecedentRow.model_fields}) for r in rows]
    return PrecedentsResult(
        ingredient=rows[0]["ingredient_name"], unii=rows[0].get("unii"), cas_number=rows[0].get("cas_number"),
        rows=out, truncated=len(rows) >= 500,
        summary=f"{len(out)} IID entries across {len({(r.route, r.dosage_form) for r in out})} route/dosage-form pairs "
                f"({ds.label} release).",
        dataset=ds)


@ref.tool(title="Browse IID vocabulary", tags={"ref", "iid"}, annotations=READ_ONLY_REMOTE, version="1.0", timeout=30)
async def ref_list_vocab(
    kind: Literal["route", "dosage_form", "potency_unit"],
    contains: Annotated[str | None, Field(max_length=60, description="Optional text the value must contain.")] = None,
) -> VocabResult:
    """List the exact FDA routes, dosage forms or units used in the IID, with how many entries use each."""
    c = _client()
    ds = await _iid_dataset(c)
    return VocabResult(kind=kind, values=await c.rpc("list_vocab", {"kind": kind, "contains": contains}), dataset=ds)


@ref.tool(title="Reference datasets available to you", tags={"ref"}, annotations=READ_ONLY_REMOTE, version="1.0", timeout=30)
async def ref_dataset_status() -> DatasetStatusResult:
    """Which reference datasets your account can read, with each release label, source URL, file hash and row count."""
    rows = await _client().rpc("dataset_status")
    return DatasetStatusResult(
        datasets=[{**r} for r in rows],
        note="Only datasets your roles grant are listed." if rows else
             "No datasets visible: ask an administrator to assign you a role (viewer, formulation, generics, sourcing).")
