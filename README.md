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

## Validation
Tests are built from the guidelines' own worked examples and tables:
- Q3A(R2) and Q3B(R2) Attachment 2 examples (rounding + threshold comparison), Attachment 1 band edges
- Q3C(R9) Tables 1–4 and both acetonitrile Option 1/2 examples
- Q3D(R2) Tables A.2.1, A.2.2, A.5.1 and the Appendix 4 Options 1 / 2a / 2b example

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
Nitrosamine CPCA (two engines + FDA list check), ICH M7, stability (Q1E), CDISC CORE, DailyMed label diffs. See `PLAN.md` and `DESIGN.md`.
