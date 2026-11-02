# OpenGxP: open-source MCP servers and agents for the regulated half of drug development

**One line:** Everyone has built MCP servers for *looking things up* in drug discovery (PubMed,
ChEMBL, UniProt, trials, FAERS). Almost nobody has built tools for the *regulated work* that
follows: CMC, quality, clinical data standards, labelling, submission. That's where pharma spends
most of its people and money. OpenGxP turns ICH/FDA guidelines into deterministic, cited,
testable tools that any agent (Claude, GPT, open models) can call.

---

## 1. The chain from discovery to market: what's covered vs. what's missing

Legend: 🟢 crowded (several open MCPs exist) · 🟡 partial · 🔴 gap (no MCP/agent found)

| # | Stage | What happens | Owner | Existing MCP / agent coverage | Status |
|---|---|---|---|---|---|
| 1 | Target discovery | Disease biology, target evidence | Discovery biology | BioMCP, ToolUniverse (600+ tools), OpenTargets/UniProt MCPs, Anthropic Life Sciences connectors | 🟢 |
| 2 | Hit → lead | Compound search, SAR, ADMET prediction | Med chem | ChEMBL/PubChem MCPs, ToolUniverse ML models | 🟢 |
| 3 | Lab work | ELN, assays, omics | Research | Benchling, 10x Genomics connectors | 🟢 |
| 4 | Preclinical safety | Tox, SEND datasets | Toxicology | CDISC CORE engine covers SEND, but no MCP | 🟡 |
| 5 | **CMC: drug substance/product** | Impurity limits, residual solvents, elemental impurities, mutagenic impurities, nitrosamines | Analytical Dev / CMC Regulatory | **None.** FDA's open `featurize-nitrosamines` and Novartis' CPCA tool exist as standalone apps only | 🔴 |
| 6 | **Stability** | Shelf-life from stability data (ICH Q1E statistics), OOT trending | QC / Stability | **None found** | 🔴 |
| 7 | Clinical trial design | Protocol, eligibility, trial search | Clin Dev | ClinicalTrials.gov MCPs | 🟢 |
| 8 | **Clinical data standards** | SDTM/ADaM conformance, define.xml | Clinical Data Mgmt / Programming | CDISC CORE is open (MIT) but **no MCP**; a PharmaSUG 2026 paper loops LLM mapping → CORE | 🔴 |
| 9 | Pharmacovigilance | FAERS signals, PRR/ROR/EBGM | Drug Safety | Several FAERS MCPs, NexVigilant (1,958 tools) | 🟢 |
| 10 | **Regulatory submission** | eCTD structure, module checks, Q&A against guidelines | Regulatory Ops | **None found** | 🔴 |
| 11 | **Labelling** | SPL/DailyMed parsing, label-change diffs, class-labelling consistency | Labelling / Reg Affairs | Partial: label *search* only (Pharma-MCP) | 🟡 |
| 12 | **Manufacturing & release** | Batch record review, deviation triage, CAPA, data integrity | QA / Manufacturing | **None found** | 🔴 |

**Conclusion:** stages 1–3, 7 and 9 are crowded. **Stages 5, 6, 8, 10–12 are open.** They're also
where regulators issue the most findings and where the work is rule-heavy, which suits deterministic tools.

---

## 2. Design principle: "guideline as code"
Each tool:
1. implements **one** guideline section deterministically (no LLM inside the calculation);
2. returns the answer **plus the clause it came from** (e.g. "ICH Q3A(R2) Attachment 1, MDD ≤ 2 g/day → identification threshold 0.10% or 1.0 mg/day TDI, whichever is lower");
3. ships with a **test suite built from the guideline's own worked examples**. That is what makes pharma trust it (a validation mindset);
4. is exposed as an **MCP server** (Python, `fastmcp`) and as a plain Python library.

LLM agents do the reading, planning and writing; OpenGxP tools do the maths and the rules.

---

## 3. The suite (v1 = what's buildable in 6–8 weeks)

### Server A: `cmc-impurities-mcp` (flagship) 🔴
| Tool | Guideline | Notes |
|---|---|---|
| `impurity_thresholds(mdd, type)` | ICH Q3A(R2) / Q3B(R2) | reporting / identification / qualification thresholds |
| `residual_solvent_limit(solvent, mdd)` | ICH Q3C(R9) | class, PDE, ppm limit (option 1/2) |
| `elemental_impurity_pde(element, route)` | ICH Q3D(R2) | PDEs by route, component approach |
| `nitrosamine_cpca(smiles)` | FDA CPCA guidance (2023, rev.) | wraps FDA `featurize-nitrosamines` (open source); returns category + AI limit + the features that drove it; cross-checked against FDA's published category list |
| `nitrosation_risk_sites(api_smiles)` | FDA/EMA nitrosamine guidance | finds secondary/tertiary amines that could form NDSRIs |
| `m7_class_hint(smiles)` | ICH M7(R2) | structural-alert flagging only (clearly labelled "not a QSAR prediction") |

### Server B: `stability-mcp` 🔴
| Tool | Guideline |
|---|---|
| `shelf_life(data, spec)` | ICH Q1E: regression, 95% one-sided confidence bound, poolability tests (p = 0.25), extrapolation rules |
| `oot_trend(data)` | Out-of-trend detection (regression-control-chart approach) |
| `climatic_zone_conditions(zone)` | ICH Q1A(R2) / WHO zones |

### Server C: `cdisc-core-mcp` 🔴
- Wraps the open CDISC CORE rules engine: `validate_sdtm(datasets)`, `explain_rule(rule_id)`, `suggest_fix(issue)`.
- Agent demo: **self-correcting SDTM mapping**. The LLM maps raw → SDTM, CORE validates, failures are fed back, repeat until clean.

### Server D: `label-mcp` 🟡→🔴
- `spl_sections(setid)`: parse DailyMed SPL XML into structured sections.
- `label_diff(setid, v1, v2)`: what changed between label versions (safety-labelling changes).
- `class_consistency(drug_class, section)`: compare the same section across a drug class (e.g. all SGLT2 boxed warnings).

### Later (v2): `ectd-mcp` (eCTD backbone/structure checks), `gmp-qa-mcp` (deviation triage, batch-record checks).

---

## 4. Agents on top (the showcase)
1. **ImpurityScope:** unknown peak → candidate structures (from the earlier plan) → `cmc-impurities-mcp`
   says whether it needs ID/qualification, and whether it's a nitrosamine with what limit → cited report.
2. **Shelf-Life Analyst:** upload stability CSV → `stability-mcp` → shelf-life justification paragraph
   with plots, written the way it would appear in CTD module 3.2.P.8.
3. **SDTM Fixer:** raw clinical data → SDTM via the self-correcting loop with `cdisc-core-mcp`.
4. **Label Watch:** weekly diff of labels for a drug class → summary of new safety language.

---

## 5. Stack ($0)
| Layer | Choice |
|---|---|
| MCP servers | Python 3.12, `fastmcp`, `pydantic` (typed inputs/outputs) |
| Chemistry | `rdkit`; FDA `featurize-nitrosamines` (Java CLI, called via subprocess) |
| Stats | `numpy`, `scipy`, `statsmodels` (Q1E regression/poolability) |
| Clinical standards | `cdisc-rules-engine` (MIT) |
| Labels | DailyMed SPL web services (free), `lxml` |
| Agents | gpt-oss-120b via Groq free tier (cached); works with Claude/any MCP client |
| UI | Next.js or Astro on **Cloudflare Pages** (free), API via Cloudflare Workers / small Python backend |
| Tests / trust | `pytest` with guideline worked examples; CI on GitHub Actions (free for public repos) |
| Docs | MkDocs Material on GitHub Pages: each tool page cites its guideline clause |

Hosting note: Python + RDKit + Java don't run on Cloudflare Workers. The demo backend needs
a small server (DigitalOcean droplet only if you have credits; otherwise Hugging Face Spaces free tier).
The MCP servers themselves are installed locally by users (`uvx cmc-impurities-mcp`), so no hosting cost.

---

## 6. Plan (8 weeks)
| Week | Deliverable |
|---|---|
| 1 | Repo, `fastmcp` skeleton, Q3A/Q3B/Q3C/Q3D tools + tests from guideline examples |
| 2 | Nitrosamine CPCA wrapper + validation against FDA's published category list (report agreement %) |
| 3 | `stability-mcp` (Q1E) + validation against the Q1E worked examples |
| 4 | `cdisc-core-mcp` + SDTM Fixer loop on CDISC's public pilot data |
| 5 | `label-mcp` (SPL parse + diff) |
| 6 | Showcase agents (ImpurityScope + Shelf-Life Analyst) |
| 7 | UI + docs site + PyPI release (`uvx` install) |
| 8 | Launch: write-up "Guideline-as-code: open MCP tools for the regulated half of pharma", LinkedIn, submit to MCP registries, PharmaSUG/DIA abstract |

---

## 7. Why this builds credibility
- It targets the work pharma actually pays for: CMC, quality, clinical data, regulatory, not another literature-search bot.
- **Validation numbers** ("CPCA tool agrees with FDA's published categories on N/N compounds"; "Q1E tool reproduces all guideline examples") speak a QA person's language.
- Open source + installable in one line, so people will actually use it and star it.
- Composable: Claude/Veeva/Medidata-style agents can call it, which positions you as the person who builds pharma agent infrastructure.

## 8. Risks / honesty
- **Not regulatory advice.** Every output carries "decision support; verify against current guidance". Guidelines get revised, so pin versions.
- CPCA edge cases exist (USP forum reports a beta-methyl misclassification in one free calculator), so the FDA-list validation is mandatory.
- Vendors (Lhasa, Veeva, Certara) may have closed tools that do parts of this. The open + MCP + cited angle is the differentiator.
- Before naming: check "OpenGxP" for existing projects/trademarks.
