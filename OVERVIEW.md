# OpenGxP: business need, what is built, and what is left

*Status as of 18 Nov 2026, version 0.1.0, 20 commits on `main` at
[github.com/milangeorge2000/opengxp](https://github.com/milangeorge2000/opengxp).*

---

## 1. The business need

### The problem
A medicine passes through many hands between discovery and the pharmacy shelf. AI tooling covers only the first part of that journey.

| Part of drug development | What the work is | AI / MCP tooling today |
|---|---|---|
| Discovery: targets, compounds, literature, trials, safety databases | Looking things up | **Crowded.** BioMCP, ToolUniverse (600+ tools), Anthropic Life Sciences connectors, several FAERS servers |
| **Regulated work:** CMC (chemistry, manufacturing and controls), quality control, stability, clinical data standards, labelling, submission | Applying ICH / FDA / CDISC rules exactly, and showing your working | **Almost nothing.** Some standalone calculators and desktop apps; no MCP servers |

The regulated half is where pharma spends most of its people and money, and where regulators issue most of their findings. The work follows rules, so it is well suited to deterministic tools. Today it is done by hand, in spreadsheets or in expensive closed software:

- **Impurity limits (CMC).** Analysts look up ICH Q3A/Q3B/Q3C/Q3D tables for every batch, every dose and every solvent. Mistakes lead to failed specifications or regulator queries.
- **Shelf life (stability).** Q1E statistics are usually done in SAS/JMP templates or by consultants. The rules on how far you may extrapolate beyond your data are easy to misapply.
- **Clinical data conformance.** SDTM datasets must pass CDISC conformance rules before an FDA submission. CDISC's open CORE engine exists, but it is a command-line tool that produces large spreadsheets, with no way for an AI agent to use it.

### Why AI alone is not enough
A general LLM asked "what is the qualification threshold at 0.8 g/day?" may answer correctly, may round differently from the guideline, or may confuse Q3A with Q3B. In a GxP setting an answer that is usually right is useless. It has to be exactly right, traceable to a clause, and reproducible.

### What OpenGxP is
**Guideline as code:** open-source MCP servers that turn ICH and CDISC rules into exact, tested tools that any AI agent (Claude, GPT, open models) or any Python program can call.

- The LLM agent reads, plans and writes. OpenGxP does the maths and applies the rules.
- Every answer carries the guideline section it applied, the guideline version, the assumptions made, a hash of the inputs (same input, same answer) and the worked examples that code is tested against.
- Built for a validation mindset: the tests are the guideline's own worked examples, wherever the guideline provides them.

### Who uses it
- **CMC / Analytical Development / QC analysts:** impurity specifications and residual-solvent and elemental-impurity risk assessments.
- **Stability and Regulatory CMC:** shelf-life justification for CTD module 3.2.P.8.
- **Clinical data management / statistical programming:** SDTM conformance checks before submission.
- **AI platform teams in pharma:** ready-made, auditable tools for their agents, instead of prompting a model to recall guideline tables.

### Why it matters (value)
- **Speed:** a full Q3D component risk assessment or a Q1E poolability analysis takes one tool call instead of hours in spreadsheets.
- **Fewer errors:** the guideline's own rounding and comparison rules are applied exactly. For example, Q3A says a reported 0.12% at 0.8 g/day is 0.96 mg, which rounds to 1.0 mg and so does **not** exceed the 1.0 mg threshold. A general LLM often gets this wrong.
- **Traceability:** every number comes with its clause, which is what QA and regulators ask for.
- **Open and free** (Apache-2.0). Closed alternatives (Lhasa, Certara/Pinnacle 21, SAS templates) are not callable by agents and are not open.

> **Not regulatory advice.** Every result says: *decision support only; verify against the current guideline and your site SOPs.*

---

## 2. What is built

One MCP server, `opengxp` (`uv run opengxp`), mounts three sub-servers: **12 tools, 7 resources and 3 prompts.**

### Shared core (`opengxp.core`)
- `GxPResult` envelope on every tool: `summary`, `findings` (info / warning / action_required with stable codes), `assumptions`, `citations` (guideline, section, rule, URL), `provenance` (tool version, guideline version, SHA-256 of inputs, validation sets) and a `disclaimer`.
- Exact half-up decimal rounding, as ICH examples require. Python's `round()` rounds halves to even and gets them wrong.
- Errors the agent can act on: for example "Unknown solvent 'dichloromethan'. Did you mean: Dichloromethane (75-09-2)?"
- MCP annotations (`readOnlyHint`, `idempotentHint`, `openWorldHint`), tags, metadata, versions, timeouts and typed output schemas.

### Server 1: `cmc` (impurities: ICH Q3A, Q3B, Q3C, Q3D)
| Tool | What it answers | Validated against |
|---|---|---|
| `cmc_impurity_thresholds` | Reporting, identification and qualification thresholds at a given dose, and which impurities cross them. Says whether the % limit or the daily-intake (TDI) limit applies, and uses the guideline's rounding rules | Q3A(R2) and Q3B(R2) Attachment 2 worked examples; every row and band edge of Attachment 1 |
| `cmc_residual_solvent_limit` | Class, PDE, Option 1 and Option 2 limits, pass/fail, and the Option 2 sum across components for 72 solvents. Accepts names, CAS numbers or synonyms | Q3C(R9) Tables 1–4; both acetonitrile examples in section 3.3; 70 of 72 CAS numbers cross-checked in PubChem (the other 2 are mixtures) |
| `cmc_elemental_impurity_risk` | Which elements to assess for each route; PDEs; Option 1, 2a, 2b and 3 limits; % of PDE; the 30% control threshold; which component contributes most; the skin-sensitiser limit (CTCL) for Ni and Co on cutaneous products | Q3D(R2) Tables A.2.1, A.2.2 and A.5.1; the Appendix 4 example tablet across Options 1, 2a and 2b |

Resources: `ich://q3c/solvents`, `ich://q3c/solvents/{name|CAS}`, `ich://q3d/elements`, `ich://q3d/elements/{symbol}`. Prompt: `impurity_assessment_report`.

### Server 2: `stab` (stability: ICH Q1E, Q1A(R2))
| Tool | What it answers | Validated against |
|---|---|---|
| `stab_estimate_shelf_life` | Batch poolability (ANCOVA: slopes then intercepts, α = 0.25); the most reduced model; the earliest time the 95% confidence bound meets the specification (one- or two-sided); the shortest batch; capped by the Q1E extrapolation limits. Flags curvature, outliers and thin data. Accepts structured points or CSV | statsmodels (p-values, slopes, residual SD and bounds agree to 1e-9); analytic crossing times; log-transform case |
| `stab_extrapolation_limit` | How far the shelf life (Y) may go beyond the long-term data (X), with the exact decision path | Every branch of Q1E sections 2.4 and 2.5 (room temperature, refrigerated, freezer, below −20 °C) |
| `stab_detect_oot` | Results that are within spec but out of trend: regression control chart, by time point, or slope control | Hand-computed prediction intervals |
| `stab_storage_conditions` | Long-term, intermediate and accelerated conditions; minimum months at submission; definition of "significant change"; semi-permeable and refrigerated notes; WHO climatic zones | Q1A(R2) sections 2.1.7 and 2.2.7 transcribed |

Resources: `ich://q1a/significant-change`, `who://climatic-zones`. Prompt: `shelf_life_justification`.

### Server 3: `sdtm` (clinical data standards: CDISC CORE)
This server wraps the **official open-source CDISC CORE command-line tool** (MIT) instead of reimplementing it.

| Tool | What it does |
|---|---|
| `sdtm_core_status` | Is CORE installed, and which version; setup steps if not |
| `sdtm_validate` | Runs CORE on SDTM / SEND / ADaM datasets, with optional Define-XML, CT packages and rule subset. Returns issues grouped by rule with counts, CDISC and FDA rule IDs and example records, and stores the full report |
| `sdtm_get_run` | Re-pages or re-filters a stored run (by domain, rule or dataset) without re-running CORE |
| `sdtm_explain_rule` | What a rule checks: message, IG references and sections, check logic |
| `sdtm_diff_runs` | Issues fixed, new and unchanged per rule between two runs, comparing only rules that ran in both; `progress` and `clean` flags drive a fix loop |

Resource: `cdisc://runs/{run_id}/report`. Prompt: `sdtm_fix_loop` (a self-correcting conformance loop with a change log).

**Real-data proof:** CORE 1.0.0 was run through OpenGxP on the public CDISC pilot study SDTM data: 9 domains, 509 rules, about 12 seconds, 11,905 issues in 27 rules. That run found and fixed two real bugs:
- a re-check of a few rules was being counted as "fixed" issues;
- CORE returns several rule IDs joined in one string, which now get split.

### Tests
- **120 unit tests pass.** One more integration test runs the real CORE on the pilot data; it is skipped unless `OPENGXP_CORE` and `OPENGXP_PILOT_SDTM` are set, and passes when they are.
- **Protocol-level tests** call the tools through an MCP client. They check output schemas, annotations, structured output, error messages and resources.

---

## 3. Is everything complete? (Design vs built)

Legend: ✅ done · 🟡 partial / changed · ❌ not built

### Server 1: CMC impurities
| Planned (DESIGN.md) | Status | Note |
|---|---|---|
| A1 Q3A/Q3B thresholds | ✅ | |
| A2 Q3C residual solvents | ✅ | |
| A3 Q3D elemental impurities | ✅ | |
| A4 Nitrosamine potency (FDA CPCA) | ❌ | Stopped. A safety classifier interrupted writing the scoring engine. A validation set of 247 FDA-published structures and categories exists in `tests/fixtures/` (uncommitted). Options: wrap FDA's own Java tool (needs a Java runtime), write the scoring by hand from FDA Appendix A, or leave it out |
| A5 Nitrosation risk sites | ❌ | Depends on A4 |
| A6 ICH M7 mutagenic impurity limits | ❌ | Not started; the limit tables are straightforward to add |
| A7 `assess_impurity_profile` (one call that runs all of them) | ❌ | Not started |
| Resource: FDA nitrosamine limits list | ❌ | Depends on A4 |
| Resource: per-tool validation report (`opengxp://validation/{tool}`) | ❌ | |
| Prompt `ndsri_risk_memo` | ❌ | Depends on A4 |

### Server 2: Stability
| Planned | Status | Note |
|---|---|---|
| B1 Shelf-life estimate (Q1E) | ✅ | Returns plot data (`curve`) rather than a rendered image |
| B2 Out-of-trend detection | ✅ | |
| B3 Storage conditions | ✅ | |
| Extrapolation as its own tool | ✅ | Added beyond the design |
| Multi-factor designs (strength × container, Q1E B.3) and bracketing/matrixing (Q1D) | ❌ | Only one factor (batch) is supported |

### Server 3: CDISC CORE
| Planned | Status | Note |
|---|---|---|
| C1 `sdtm_validate` | ✅ | Long runs report progress; they do not use MCP background tasks |
| C2 `sdtm_explain_rule` | ✅ | |
| C3 `sdtm_diff_runs` | ✅ | |
| Status and paging tools | ✅ | Added beyond the design |
| Resource: rule catalogue per standard | ❌ | `sdtm_explain_rule` covers single rules |
| SDTM Fixer agent (does the mapping itself) | 🟡 | The prompt exists. A working agent demo on raw data is not built |

### Server 4: Label (DailyMed SPL)
| Planned | Status |
|---|---|
| D1 `label_search`, D2 `label_sections`, D3 `label_diff`, D4 `label_class_compare` | ❌ Not started |

### Cross-cutting
| Planned | Status | Note |
|---|---|---|
| Shared envelope, citations, provenance, input hash | ✅ | |
| `detail: concise / full` on every tool | 🟡 | Only `cmc_impurity_thresholds` has it |
| `guideline_version` input to pin a version | 🟡 | The version is reported in provenance but cannot be chosen; only one version of each guideline is encoded |
| Gateway mounting all servers | ✅ | |
| Middleware (structured logging, response-size guard) | ❌ | |
| GitHub Actions CI | ❌ | No `.github/` workflows yet |
| PyPI release (`uvx opengxp`) | ❌ | Runs from a clone today (`uv run opengxp`) |
| Docs site (MkDocs) | ❌ | README and this overview only |
| Showcase agents (ImpurityScope, Shelf-Life Analyst, Label Watch) | ❌ | |
| Web UI on Cloudflare Pages | ❌ | |
| v2 servers (eCTD, GMP QA) | ❌ | |

### Plan progress (8-week plan in PLAN.md)
| Week | Deliverable | Status |
|---|---|---|
| 1 | Repo, skeleton, Q3A/Q3B/Q3C/Q3D + tests | ✅ |
| 2 | Nitrosamine CPCA + FDA list validation | ❌ blocked (see A4) |
| 3 | Stability (Q1E) | ✅ |
| 4 | CDISC CORE + fix loop | ✅ (loop as a prompt; agent demo not built) |
| 5 | Label server | ❌ |
| 6 | Showcase agents | ❌ |
| 7 | UI, docs site, PyPI | ❌ |
| 8 | Launch write-up, registries, PharmaSUG/DIA abstract | ❌ |

**Bottom line:** 3 of the 4 planned servers are working and validated (CMC Q3A–Q3D, stability, CDISC CORE), with 12 tools. Still missing:
- the nitrosamine/M7 half of the CMC server;
- the label server;
- packaging (CI, PyPI);
- the showcase agents and UI.

---

## 4. Known limits and risks
- **Not regulatory advice.** Guidelines change. Q3C(R10) is in progress, and ICH is consolidating Q1A–Q1E into one new Q1 guideline (I couldn't confirm whether Step 4 was reached this month). Each encoded version is named in every result.
- **Q1E has no numeric worked examples.** Its examples are graphs, so stability is validated against statsmodels and analytic cases rather than guideline numbers.
- **Out-of-trend methods** come from the 2003 PhRMA paper, not from ICH. The tool says so.
- **CDISC CORE behaviour is CORE's.** On the pilot data, 48 rules failed inside CORE (for example a date-casting error) and are reported separately. The pilot data is SDTMIG 3.1.2, but CORE's oldest rule set is 3.2, so those issue counts are not findings about the pilot study.
- **CDISC CORE is an optional install** (`opengxp[cdisc]`, Python 3.12 only) plus a separate CORE release download.

## 5. Recommended next steps
1. **GitHub Actions CI and a PyPI release.** This makes the repo installable with one line and shows a green test badge; it is the biggest credibility gain for the least work.
2. **ICH M7 limits and `cmc_assess_impurity_profile`.** These complete the CMC server without the nitrosamine engine.
3. **Label server (DailyMed).** It is the only planned server not started.
4. **Decide the nitrosamine route:** wrap FDA's Java tool, hand-write it, or drop it.
5. **One showcase agent and a short demo video,** then the launch write-up.
