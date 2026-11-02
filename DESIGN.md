# OpenGxP: server and tool design (v1)

Status: design. Nothing built yet. Every numeric table below **must be re-checked against the
guideline PDF when it is encoded**. The tests are the source of truth, not this document.

---

## 0. What "state of the art" means for an MCP server in late 2026

| Practice | How OpenGxP applies it |
|---|---|
| **MCP 2025-11-25 spec**: `outputSchema` + `structuredContent`, tool `annotations`, `icons`, `title`; experimental `tasks` for long jobs | Every tool returns typed structured output (pydantic). Long CORE validations run as tasks (`task=True`) |
| **FastMCP 3**: `tags`, `meta`, `version`, `timeout`, `ToolResult`, `ToolError`, `Context`, `mount()` | Used as shown in §2. One gateway server mounts all four sub-servers with prefixes |
| **Workflow-shaped tools, not API mirrors.** Few tools that match a user's job, plus small lookup tools | Each server has 1 "assess" tool (the job) + 2–4 primitives |
| **LLM-friendly inputs**: enums over free text, explicit units, names *or* IDs accepted, sensible defaults | `Route`, `Unit`, `GuidelineVersion` enums; solvents by name/CAS/synonym with "did you mean" |
| **Concise vs detailed output** (`response_format`) to save context | Every tool takes `detail: "concise" \| "full"` |
| **Actionable errors**: tell the model what to do next | `ToolError("Unknown solvent 'DCM2'. Did you mean: dichloromethane (75-09-2)? Call cmc_list_solvents to browse.")` |
| **Batch inputs** to cut round trips | Impurity tools take a *list* of impurities; label diff takes a list of set IDs |
| **Provenance** on every answer | Common `Citation` + `Provenance` envelope (§1) |
| **Pinned versions** | `guideline_version` input (default = latest encoded), echoed in output |
| **Resources** for reference data; **prompts** for whole workflows | `ich://q3c/solvents/{id}`, `ich://q3d/elements/{symbol}`; prompts `impurity_assessment_report` etc. |
| **Deterministic & offline** where possible | CMC and stability servers make no network calls (`openWorldHint=false`) |

---

## 1. Shared envelope (`opengxp_core` package)

Every tool output inherits from `GxPResult`. This makes all four servers behave the same, and
it's the "validation mindset" in code: the agent always knows *which clause*, *which version*,
*what was assumed*, and *what to check by hand*.

```python
class Citation(BaseModel):
    guideline: str            # "ICH Q3A(R2)"
    section: str              # "Attachment 1"
    clause_text: str | None   # short paraphrase of the rule applied (not verbatim)
    url: HttpUrl | None       # official PDF

class Provenance(BaseModel):
    tool: str                 # "cmc_impurity_thresholds"
    tool_version: str         # package version, e.g. "0.3.1"
    guideline_version: str    # "Q3C(R9)"
    inputs_hash: str          # sha256 of canonical inputs: same input -> same hash -> same answer
    computed_at: datetime
    validated_against: list[str]  # test sets this code path passes, e.g. ["Q3A-Att1-examples", "FDA-CPCA-list-2025-09"]

class Finding(BaseModel):
    level: Literal["info", "warning", "action_required"]
    code: str                 # stable machine code, e.g. "ABOVE_QUALIFICATION"
    message: str              # one sentence a QA reviewer understands

class GxPResult(BaseModel):
    summary: str              # 1–2 sentence plain-language answer (also sent as text content)
    findings: list[Finding] = []
    assumptions: list[str] = []      # e.g. "MDD taken as 0.5 g/day as supplied"
    citations: list[Citation]
    provenance: Provenance
    disclaimer: str = "Decision support only. Verify against the current guideline and your site SOPs."
```

Shared enums: `Route` (`oral`, `parenteral`, `inhalation`, `cutaneous`), `DoseUnit` (`mg`, `g`),
`Detail` (`concise`, `full`).

Shared decorator defaults (one helper, `gxp_tool(...)`):
```python
@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True,
                 "destructiveHint": False, "openWorldHint": False},
    tags={"cmc", "ich-q3a"},
    meta={"guideline": "ICH Q3A(R2)", "validated": True, "not_regulatory_advice": True},
    version="1.0",
    timeout=30,
)
```

Errors: `ToolError` for anything the model can fix (bad unit, unknown solvent, invalid SMILES),
always with the fix. Unexpected exceptions are masked (`mask_error_details=True`).

---

## 2. Server A: `cmc-impurities` (flagship)

Prefix `cmc_`. Offline, deterministic. Deps: `rdkit`, Java 17 (CPCA jar).

### A1. `cmc_impurity_thresholds`: ICH Q3A(R2) / Q3B(R2)
**Job:** "At this dose, what are my reporting / identification / qualification thresholds, and which of my impurities cross them?"

| Arg | Type | Notes |
|---|---|---|
| `product_type` | `"drug_substance" \| "drug_product"` | picks Q3A vs Q3B |
| `max_daily_dose` | `float > 0` | |
| `dose_unit` | `"mg" \| "g"` | no silent unit guessing |
| `impurities` | `list[ImpurityObs]` (optional, ≤200) | `{name, level_percent, is_degradant?: bool, rrt?: float}` |
| `detail` | `concise \| full` | |
| `guideline_version` | `"Q3A(R2)" \| "Q3B(R2)"` | default latest |

Output `ThresholdResult(GxPResult)`:
- `thresholds`: `{reporting, identification, qualification}`, each `{percent, tdi_mg_per_day | None, binding: "percent" | "tdi"}` (the "whichever is lower" rule made explicit)
- `per_impurity`: `[{name, level_percent, tdi_mg_per_day, exceeds: ["reporting","identification",...], next_action}]`
- `next_action` enum: `report_only`, `identify`, `qualify`, `none`.

Encoded rules (verify in tests):
- **Q3A:** MDD ≤ 2 g/day: report 0.05%; identify 0.10% or 1.0 mg/day TDI (lower); qualify 0.15% or 1.0 mg/day (lower). MDD > 2 g/day: 0.03% / 0.05% / 0.05%.
- **Q3B:** reporting 0.1% (≤1 g) / 0.05% (>1 g). Identification and qualification use the four dose bands in Attachment 1 (e.g. identification < 1 mg: 1.0% or 5 µg TDI; qualification < 10 mg: 1.0% or 50 µg TDI…).
- Q3B applies to **degradation products only**; a process impurity on `drug_product` gives a warning `Q3B_SCOPE`.
- Edge cases tested: dose exactly on a band boundary; TDI binding vs percent binding; rounding conventions (Q3A Att. 2 reporting decimals).

### A2. `cmc_residual_solvent_limit`: ICH Q3C(R9)
| Arg | Type | Notes |
|---|---|---|
| `solvents` | `list[str]` | name, CAS or synonym ("DCM", "75-09-2", "methylene chloride") |
| `option` | `"option1" \| "option2"` | default `option1` (10 g/day assumption) |
| `daily_dose_g` | `float` | required only for option 2 |
| `measured_ppm` | `dict[str, float]` (optional) | returns pass/fail |
| `components` | `list[{name, mass_g, solvent_ppm: dict}]` (optional) | Option 2 summation across excipients |

Output: per solvent `{canonical_name, cas, class: 1|2|3|"no_pde", pde_mg_per_day, limit_ppm, measured_ppm?, pass?}`; for Option 2, the total daily intake vs PDE.
- Option 1: `ppm = 1000 × PDE / 10`. Class 1 fixed limits (benzene 2 ppm, CCl₄ 4 ppm…). Class 3: 50 mg/day (5,000 ppm) without justification.
- Name resolution: exact, then CAS, then synonyms table, then fuzzy (`rapidfuzz`) with "did you mean" in `ToolError`.

### A3. `cmc_elemental_impurity_risk`: ICH Q3D(R2)
**Job:** component approach. "Given my formulation and what I know about each component, which elements need control?"

| Arg | Type |
|---|---|
| `route` | `Route` (incl. cutaneous, added in R2) |
| `daily_dose_g` | `float` |
| `components` | `list[{name, mass_per_dose_g, element_ppm: dict[str, float]}]` |
| `elements_of_interest` | `list[str] \| None` (default = Q3D risk-assessment list for route: class 1, 2A, and 2B/3 if intentionally added) |
| `control_threshold_fraction` | `float = 0.30` |
| `option` | `"option1" \| "option2a" \| "option2b" \| "option3"` |

Output: per element `{class, pde_ug_per_day, total_intake_ug, percent_of_pde, above_control_threshold, permitted_conc_ug_per_g}`, plus a ranked `drivers` list (which component contributes most).

### A4. `cmc_nitrosamine_potency`: FDA CPCA (flagship of the flagship)
| Arg | Type | Notes |
|---|---|---|
| `structures` | `list[str]` (≤50) | SMILES; must contain N-N=O, else `ToolError` with a hint to call A5 |
| `region` | `"US" \| "EU" \| "other"` | changes PC1 limit (26.5 vs 18 ng/day) |
| `max_daily_dose_mg` | `float \| None` | if given, convert AI ng/day → **ppm limit in API** |
| `include_feature_trace` | `bool = True` | show *why* |

Output per structure:
```
{ smiles_canonical, alpha_h_counts: "2,3", alpha_h_score,
  features: [{name: "N-nitroso in pyrrolidine ring", score: +3, atoms: [..]}],
  auto_pc5_reason: str | None, potency_score, category: 1..5,
  ai_limit_ng_per_day, limit_ppm | None,
  fda_published_ai: {value, source_date} | None,   # if FDA's table lists this compound, show it
  agrees_with_fda_list: bool | None,
  engine: {primary: "featurize-nitrosamines vX", cross_check: "opengxp-rdkit", agree: bool} }
```
- Categories: score ≤1 → PC1, 2 → PC2 (100), 3 → PC3 (400), 4 → PC4 (1500), ≥5 → PC5 (1500). Auto-PC5 for α-H 0,0 / 0,1 / 1,1 or tertiary α-carbon.
- **Two engines.** It runs FDA's Java tool *and* an independent RDKit SMARTS implementation; disagreement gives a `Finding(level="action_required", code="ENGINE_DISAGREE")`. This is the killer trust feature.
- **FDA list first.** If FDA has published a compound-specific AI, that wins, and the output says so.

### A5. `cmc_nitrosation_risk_sites`
**Job:** "Can my API (or an amine impurity) form an NDSRI?"
- Args: `api_smiles: str`, `include_predicted_ndsri: bool = True`, `region`.
- Output: `sites: [{atom_idx, amine_type: "secondary"|"tertiary"|"dimethylamino", vulnerable: bool, predicted_ndsri_smiles, predicted_category}]`. Predicted NDSRIs are piped through A4 internally, so one call answers "which NDSRIs and what limits".

### A6. `cmc_mutagenic_impurity_limit`: ICH M7(R2)
- Args: `impurities: list[{name, smiles?, m7_class?: 1..5}]`, `treatment_duration: "<=1m" | ">1-12m" | ">1-10y" | ">10y"`, `multiple_impurities: bool`.
- Output: per impurity `{alert_hits: [..], suggested_class_hint, acceptable_intake_ug_per_day}` using the less-than-lifetime table (120 / 20 / 10 / 1.5 µg/day) and the total-limit rule for multiple class 2/3 impurities.
- `suggested_class_hint` comes from structural alerts only and is labelled **"not a QSAR prediction; M7 requires two complementary (Q)SAR methods"**.

### A7. `cmc_assess_impurity_profile` (the "job" tool)
One call: `{product_type, route, max_daily_dose, impurities: [{name, level_percent, smiles?}], api_smiles?, solvents_ppm?, region}` → runs A1, A4/A5 (for nitroso structures), A6 (for structures with alerts) and A2. It returns one consolidated table + ranked `action_required` list. This is what an agent should call 90% of the time.

### Resources (read-only reference data, cheap for the model to browse)
- `ich://q3c/solvents` (list), `ich://q3c/solvents/{cas}`
- `ich://q3d/elements/{symbol}` (PDEs for all routes)
- `fda://cpca/published-limits` (FDA compound table, dated snapshot)
- `opengxp://validation/{tool}` (the validation report: test cases, pass rate). Agents can quote it.

### Prompts
- `impurity_assessment_report(product_name, ...)`: writes a CTD 3.2.S.3.2-style section, calling A7 and citing every number.
- `ndsri_risk_memo(api_smiles)`.

---

## 3. Server B: `stability` (ICH Q1E / Q1A)

Prefix `stab_`. Offline. Deps: `numpy`, `scipy`, `statsmodels`, `matplotlib` (plots returned as `ImageContent`).

Shared input model:
```python
class StabilityPoint(BaseModel):
    batch: str
    time_months: float = Field(ge=0)
    value: float
    condition: str = "25C/60%RH"

class Attribute(BaseModel):
    name: str                           # "Assay", "Impurity B"
    direction: Literal["decreasing", "increasing", "both"]
    lower_spec: float | None
    upper_spec: float | None
    unit: str = "%"
    transform: Literal["none", "log"] = "none"   # log for first-order degradation
```
Data can be passed inline (`points`) **or** as `csv_text` / `resource_uri` (large data never goes through the prompt twice).

### B1. `stab_estimate_shelf_life`
| Arg | Notes |
|---|---|
| `attribute`, `points` | as above, ≥3 batches recommended (warning otherwise) |
| `pooling_alpha` | default 0.25 (Q1E) |
| `confidence` | default 0.95; one-sided for `increasing`/`decreasing`, two-sided for `both` |
| `proposed_shelf_life_months` | optional, returns supported/not supported |
| `accelerated_significant_change` | `bool \| None`, needed for extrapolation |
| `intermediate_significant_change` | `bool \| None` |
| `storage` | `"room_temp" \| "refrigerated" \| "frozen"` (changes extrapolation rules) |
| `return_plot` | `bool = False` |

Output:
- `poolability`: `{slopes_p, intercepts_p, model: "separate" | "common_slope" | "pooled"}`
- `per_batch`: `[{batch, slope, intercept, crossing_month}]`
- `shelf_life_supported_months` (the earliest crossing of the CI with spec, floored to the reporting step)
- `extrapolation`: `{allowed, max_months, rule_path: ["long-term data X=12", "no significant change at accelerated", "statistics amenable", "Y ≤ 2X, ≤ X+12"]}`. This is the Q1E Appendix A decision tree as an explicit path.
- Findings such as `CURVATURE` (lack-of-fit), `OUTLIER_BATCH`, `FEW_BATCHES`.

### B2. `stab_detect_oot`
- Args: `historical: list[StabilityPoint]`, `new_points: list[StabilityPoint]`, `method: "regression_control" | "by_time_point" | "slope_control"`, `alpha=0.01`.
- Output: per new point `{oot: bool, predicted, prediction_interval, z}`.
- Cites the PhRMA CMC Stability Expert Team OOT paper (methods), not an ICH clause, and says so.

### B3. `stab_storage_conditions`
- Args: `climatic_zone: "I" | "II" | "III" | "IVa" | "IVb"`, `product: "general" | "refrigerated" | "frozen" | "semi_permeable"`.
- Output: long-term / intermediate / accelerated conditions and minimum durations at submission (Q1A(R2) + WHO).

### Prompt
- `shelf_life_justification(product)`: drafts a CTD 3.2.P.8.1 summary with plots and the rule path.

---

## 4. Server C: `cdisc-core` (clinical data standards)

Prefix `sdtm_`. Wraps the open CDISC CORE engine (`cdisc-rules-engine`, MIT).
**Setup:** the rule cache needs a free CDISC Library API key (`CDISC_LIBRARY_API_KEY`) once, for `update-cache`.
The server checks at start-up and returns a clear `ToolError` if missing.

### C1. `sdtm_validate` (`task=True`, long-running; reports progress)
| Arg | Notes |
|---|---|
| `datasets_path` | folder of `.xpt` / `.json` (Dataset-JSON) |
| `standard` | `"sdtmig" \| "sendig" \| "adam"` |
| `version` | e.g. `"3-4"`; validated against what's in the cache |
| `define_xml_path` | optional |
| `rule_ids` | optional subset (fast re-check after a fix) |
| `domains` | optional filter, e.g. `["AE","DM"]` |
| `severity_min` | `"error" \| "warning"` |
| `max_issues` / `cursor` | pagination (big studies produce thousands of issues) |
| `group_by` | `"rule" \| "domain" \| "none"`, default `rule` (one line per rule with count + 3 examples: much better for an LLM) |

Output: `{run_id, totals: {errors, warnings, rules_failed}, issues: [{rule_id, domain, variables, message, count, examples: [{row, values}]}], next_cursor, report_path}`.

### C2. `sdtm_explain_rule`
- Args: `rule_id: str` → `{rule_id, description, standard_refs (IG section), check_logic (YAML condensed), common_causes, fix_hint}`.

### C3. `sdtm_diff_runs`
- Args: `run_id_before, run_id_after` → fixed / new / unchanged issues. This is how the self-correcting loop proves progress and stops.

### Resource
- `cdisc://rules/{standard}/{version}` (rule catalogue), `cdisc://runs/{run_id}/report`.

### Agent: SDTM Fixer
The LLM maps raw → SDTM, calls `sdtm_validate`, groups by rule, calls `sdtm_explain_rule`, patches the mapping, re-runs with `rule_ids`, and `sdtm_diff_runs` until no errors or no progress in two rounds. Every change goes in a change log (a GxP must).

---

## 5. Server D: `label` (DailyMed SPL)

Prefix `label_`. **Open world** (`openWorldHint=true`), read-only, cached (`diskcache`, 24 h). Free DailyMed web services; no key.

### D1. `label_search`
- Args: `query` (drug, ingredient, NDC or set ID), `filters: {labeler?, rx_otc?: "rx"|"otc", published_after?: date, pharm_class?: str}`, `limit ≤ 25`, `cursor`.
- Output: `[{setid, title, labeler, version, effective_date, pharm_class}]`.

### D2. `label_sections`
- Args: `setid`, `version: int | "latest"`, `sections: list[SectionCode]`. The enum maps friendly names to LOINC codes: `boxed_warning` 34066-1, `indications` 34067-9, `dosage` 34068-7, `contraindications` 34070-3, `warnings_precautions` 43685-7, `adverse_reactions` 34084-4, `drug_interactions` 34073-7, `specific_populations` 43684-0. Also `max_chars_per_section`.
- Output: `{setid, version, effective_date, sections: [{code, name, text, subsections}]}`.

### D3. `label_diff`
- Args: `setid`, `from_version`, `to_version` (`"previous"` / `"latest"` allowed), `sections` filter, `granularity: "sentence" | "paragraph"`.
- Output: per section `{added: [..], removed: [..], changed: [{before, after}]}`, plus `safety_relevant: bool` (any change in boxed warning / W&P / contraindications).

### D4. `label_class_compare`
- Args: `pharm_class` (EPC, e.g. "Sodium-Glucose Cotransporter 2 Inhibitor") or `setids`, `section`, `limit ≤ 15`.
- Output: a matrix of which products carry which statements (sentence clusters), highlighting outliers, e.g. "3 of 4 SGLT2 labels mention ketoacidosis in Section 5.x; product X does not".

---

## 6. Gateway and packaging
```python
gateway = FastMCP("opengxp")
gateway.mount(cmc_server, prefix="cmc")
gateway.mount(stability_server, prefix="stab")
gateway.mount(cdisc_server, prefix="sdtm")
gateway.mount(label_server, prefix="label")
```
- Install: `uvx opengxp` (all) or `uvx opengxp-cmc` (one). Transports: stdio (local) and streamable HTTP (demo backend).
- Middleware: structured logging (inputs hash only, no data), timing, response-size guard (with the known FastMCP 3.1 bug in mind: truncation must keep `structuredContent` valid).
- Tag filtering lets an org expose only validated tools: `include_tags={"validated"}`.

## 7. Test and validation plan (what makes it credible)
| Tool | Ground truth |
|---|---|
| A1 Q3A/Q3B | Every row of Attachment 1 tables + boundary doses (property tests with `hypothesis`) |
| A2 Q3C | All Class 1/2 limits in the Q3C(R9) tables; Option 2 worked example in the guideline |
| A3 Q3D | PDE tables (all routes); the component-approach examples in Q3D Appendix |
| A4 CPCA | FDA published NDSRI AI list: report % category agreement for both engines + list of disagreements |
| A6 M7 | LTL table; multiple-impurity examples |
| B1 Q1E | Q1E worked examples; cross-check vs R/`lm` on public stability datasets |
| C | CDISC pilot study data (known issues) |
| D | Frozen SPL fixtures (recorded responses) so CI doesn't hit the network |

CI publishes `opengxp://validation/{tool}` reports, so every tool can say what it was validated against.

## 8. Build order (replaces week 1–2 of PLAN.md)
1. `opengxp_core` envelope + `gxp_tool` helper + test harness.
2. A1 → A2 → A3 (pure tables, fast wins, prove the pattern).
3. A4 + A5 with both engines + FDA-list validation report.
4. A6, A7, resources, prompts. Release `opengxp-cmc 0.1`.

## Sources
- FastMCP 3 tools docs: https://gofastmcp.com/docs/v3/servers/tools.md
- MCP spec 2025-11-25, tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- FDA CPCA overview: https://www.fda.gov/drugs/spotlight-cder-science/determining-recommended-acceptable-intake-limits-n-nitrosamine-impurities-pharmaceuticals
- FDA CPCA QMRF (links FDA/featurize-nitrosamines): https://fda.gov/media/183710/download
- Novartis CPCA tool: https://awesome.ecosyste.ms/projects/github.com%2Fnovartis%2Fndsris_in_silico_tool
- CDISC CORE engine: https://github.com/cdisc-org/cdisc-rules-engine, https://pypi.org/project/cdisc-rules-engine
