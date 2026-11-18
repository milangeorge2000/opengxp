# OpenGxP

[![CI](https://github.com/milangeorge2000/opengxp/actions/workflows/ci.yml/badge.svg)](https://github.com/milangeorge2000/opengxp/actions/workflows/ci.yml)

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

## v0.4: `ind` server (IND-enabling decisions)

The numbers that decide whether and how a drug enters humans, which today live in spreadsheets and unlicensed web calculators.

| Tool | What it does |
|---|---|
| `ind_ddi_risk` | ICH M12 (2024): per CYP, UGT and transporter, can in vitro data exclude a clinical interaction? Basic methods (Cmax,u/Ki,u < 0.02, gut CYP3A, TDI (kobs+kdeg)/kdeg < 1.25, mRNA fold-change, Table 1 transporter cutoffs), basic kinetic induction model, mechanistic static AUCR (0.80–1.25, inhibition and induction also alone), fu,p 1% floor, next step per target |
| `ind_first_in_human_dose` | FDA 2005 MRSD: NOAEL → HED (Table 1 or the 0.33-exponent formula) → most sensitive species → ÷ safety factor; ICH S9 oncology mode (1/10 STD10, 1/6 HNSTD); flags when a MABEL is needed |
| `ind_safety_margins` | hERG margin per ICH E14/S7B Q&A 1.2 (free high clinical Cmax,ss, 1% fu floor, lab-calibrated threshold, no invented cut-off) and exposure margins at the NOAEL |
| `ind_tox_duration` | ICH M3(R2) Tables 1–2: minimum rodent / non-rodent repeated-dose tox duration for a trial or for marketing, per region, with footnotes |
| `ind_metabolite_coverage` | ICH M3(R2) section 3 and Q&A: metabolites > 10% of total exposure, covered when animal exposure ≥ 50% of human (above human if the metabolite is the majority) |

Resources: `ich://m12/kdeg`, `fda://mrsd-2005/conversion-factors`. Prompt: `ind_nonclinical_summary`.

## GxP Agent Gateway (`opengxp-gateway`)

A control layer that sits in front of **any** MCP server (OpenGxP's own, or e.g. a LIMS/QMS server) so agent tool calls meet Part 11 / Annex 11 expectations. Design: [`docs/GATEWAY.md`](https://github.com/milangeorge2000/opengxp/blob/main/docs/GATEWAY.md).

- **Identity**: OAuth token claims over HTTP, or a configured local user (recorded as weaker `local-config`).
- **Role policy**: allow/deny tool globs per role; denied tools are hidden from the agent; `monitor` mode for pilots.
- **Electronic-signature approvals**: write tools (or any glob) need a named approver's signature (ID + password, printed name, meaning, reason, time); single-use, bound to the exact inputs, expiring; four-eyes by default.
- **Validated-only roles**: only tools QA registered as validated (for the running version) are callable.
- **Hash-chained audit trail**: one append-only record per call (who, which agent, tool + version, inputs or redacted hashes, output hash, approval/signature); `verify` detects edits, deletions, reordering.
- **Reports**: audit-trail review report (Markdown/CSV) and a CSA-style validation summary per tool linked to pytest JUnit results.

```bash
opengxp-gateway add-signer qa_lead --name "Dr. A. Reviewer"
opengxp-gateway serve --target opengxp --policy examples/gateway/policy.yaml --registry examples/gateway/registry.yaml
opengxp-gateway serve --target "uvx some-other-mcp-server"     # proxy any MCP server
opengxp-gateway approvals --status pending
opengxp-gateway sign APR-1A2B3C4D --signer qa_lead --meaning approved --reason "Deviation DV-12 closed"
opengxp-gateway verify && opengxp-gateway report --since 2026-11-01 > audit-review.md
```

The gateway is a control layer, not a validated system out of the box: each organisation validates it for its intended use.

## Validation
Tests are built from the guidelines' own worked examples and tables:
- Q3A(R2) and Q3B(R2) Attachment 2 examples (rounding + threshold comparison), Attachment 1 band edges
- Q3C(R9) Tables 1–4 and both acetonitrile Option 1/2 examples
- Q3D(R2) Tables A.2.1, A.2.2, A.5.1 and the Appendix 4 Options 1 / 2a / 2b example
- CORE adapter: unit-tested against a stand-in CLI writing CORE v1.0.0's JSON format, and integration-tested with the real CORE 1.0.0 release on the public CDISC pilot study SDTM data (509 rules, 9 domains, ~12 s): `OPENGXP_CORE=... OPENGXP_PILOT_SDTM=... uv run pytest tests/integration`
- IND: every ICH M12 cutoff at and around its boundary, M12 Table 3 equations and Table 6 kdeg values; FDA 2005 Table 1 and the Appendix C rabbit example; ICH S9 Note 2; every row of ICH M3(R2) Tables 1–2; M3(R2) Q&A metabolite thresholds
- Q1E: ANCOVA p-values, slopes, residual SD and confidence bounds match statsmodels to 1e-9; analytic crossing times; every branch of the extrapolation rules

```bash
uv run pytest
```

## Use it
```bash
uvx opengxp                       # stdio MCP server (all tools)
uvx --from opengxp opengxp-gateway serve --policy policy.yaml   # same tools behind the GxP gateway
pip install "opengxp[cdisc]"      # adds the CDISC CORE engine (Python 3.12)
```
From a clone: `uv run opengxp`.
Claude Desktop / Claude Code config:
```json
{ "mcpServers": { "opengxp": { "command": "uvx", "args": ["opengxp"] } } }
```

## Roadmap
ICH M7, assay data QC (plate QC, dose-response fitting), metabolite ID from MS/MS, DailyMed label diffs. See [PLAN.md](https://github.com/milangeorge2000/opengxp/blob/main/PLAN.md), [DESIGN.md](https://github.com/milangeorge2000/opengxp/blob/main/DESIGN.md) and [OVERVIEW.md](https://github.com/milangeorge2000/opengxp/blob/main/OVERVIEW.md).
