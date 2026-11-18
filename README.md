# OpenGxP

**Guideline-as-code MCP servers for the regulated half of drug development.**

Everyone has built MCP servers that *look things up* (PubMed, ChEMBL, trials, FAERS). OpenGxP builds
the tools pharma actually works under: deterministic, cited, tested implementations of ICH/FDA
guidelines that any agent (Claude, GPT, open models) can call.

> Decision support only. Verify against the current guideline and your site SOPs.

## v0.1: `cmc` server

| Tool | Guideline | What it answers |
|---|---|---|
| `cmc_impurity_thresholds` | ICH Q3A(R2) / Q3B(R2) | Reporting / identification / qualification thresholds at a dose, and which impurities cross them (with the guideline's rounding rules) |
| `cmc_residual_solvent_limit` | ICH Q3C(R9) | Class, PDE, Option 1/2 limits, pass/fail, Option 2 sum across components |
| `cmc_elemental_impurity_risk` | ICH Q3D(R2) | Elements to assess by route, PDEs, permitted concentrations, % of PDE, 30% control threshold, main contributing component, cutaneous CTCL |

Resources: `ich://q3c/solvents`, `ich://q3c/solvents/{name|CAS|synonym}`, `ich://q3d/elements`, `ich://q3d/elements/{symbol}`.
Prompt: `impurity_assessment_report`.

Every result carries: `summary`, `findings` (info / warning / action_required), `assumptions`,
`citations` (guideline + section + rule), `provenance` (tool and guideline version, sha256 of inputs,
the worked examples this code path is validated against) and a disclaimer.

## v0.2: `stab` server

| Tool | Guideline | What it answers |
|---|---|---|
| `stab_estimate_shelf_life` | ICH Q1E 2.6, Appendix B | ANCOVA batch poolability (slopes then intercepts, α = 0.25), most reduced model, earliest time the 95% confidence bound meets the criterion, shortest batch, capped by the Q1E extrapolation limits; flags curvature, outliers, thin data |
| `stab_extrapolation_limit` | ICH Q1E 2.4–2.5, Appendix A | How far beyond the long-term data (X) the shelf life (Y) may go, with the decision path |
| `stab_detect_oot` | PhRMA OOT paper (2003) | Out-of-trend results by regression control chart, by time point, or slope control |
| `stab_storage_conditions` | ICH Q1A(R2), WHO zones | Long-term / intermediate / accelerated conditions, minimum months, 'significant change' definition |

Data go in as structured points or plain CSV (`batch,time_months,value`). Resources: `ich://q1a/significant-change`, `who://climatic-zones`. Prompt: `shelf_life_justification`.

## v0.3: `sdtm` server (CDISC CORE)

Wraps the official open-source [CDISC CORE](https://github.com/cdisc-org/cdisc-rules-engine) CLI (MIT) instead of reimplementing it.

| Tool | What it does |
|---|---|
| `sdtm_core_status` | Is CORE installed, which version; setup steps if not |
| `sdtm_validate` | Runs CORE on SDTM / SEND / ADaM datasets (+ Define-XML, CT packages, rule subset); returns issues grouped by rule with counts, CDISC/FDA rule IDs and example records; stores the full report |
| `sdtm_get_run` | Re-pages / re-filters a stored run (by domain, rule, dataset) without re-running CORE |
| `sdtm_explain_rule` | What a CORE rule checks: message, IG references and sections, check logic |
| `sdtm_diff_runs` | Fixed / new / unchanged issues per rule between two runs; `progress` and `clean` flags to drive a fix loop |

Prompt `sdtm_fix_loop` runs the self-correcting conformance loop. Setup: `pip install opengxp[cdisc]` (only needed for a repo clone), download a CORE release, set `OPENGXP_CORE` to its `core` executable (or to `core.py` in a clone).

## Validation
Tests are built from the guidelines' own worked examples and tables:
- Q3A(R2) and Q3B(R2) Attachment 2 examples (rounding + threshold comparison), Attachment 1 band edges
- Q3C(R9) Tables 1–4 and both acetonitrile Option 1/2 examples
- Q3D(R2) Tables A.2.1, A.2.2, A.5.1 and the Appendix 4 Options 1 / 2a / 2b example
- CORE adapter: unit-tested against a stand-in CLI writing CORE v1.0.0's JSON format, and integration-tested with the real CORE 1.0.0 release on the public CDISC pilot study SDTM data (509 rules, 9 domains, ~12 s): `OPENGXP_CORE=... OPENGXP_PILOT_SDTM=... uv run pytest tests/integration`
- Q1E: ANCOVA p-values, slopes, residual SD and confidence bounds match statsmodels to 1e-9; analytic crossing times; every branch of the extrapolation rules

```bash
uv run pytest
```

## Use it
```bash
uv run opengxp          # stdio MCP server
```
Claude Desktop / Claude Code config:
```json
{ "mcpServers": { "opengxp": { "command": "uv", "args": ["--directory", "/path/to/opengxp", "run", "opengxp"] } } }
```

## Roadmap
ICH M7, DailyMed label diffs. See `PLAN.md` and `DESIGN.md`.
