# GxP Agent Gateway: design

## Problem
AI agents call tools (MCP) that read or change GMP-relevant data. EU GMP Annex 11 (and its 2025
revision draft), 21 CFR Part 11 and the draft Annex 22 (AI) expect:
- **attributable, time-stamped, tamper-evident records** of who did what, when, with which system version;
- **access control** by role;
- **electronic signatures** by a named human for approvals, recording the meaning of the signature;
- **validated systems** with documented intended use, risk and test evidence (CSA / GAMP 5).

Agent tool calls bypass application logs, and agents often share credentials, so QA cannot sign them off.

## What the gateway is
A FastMCP middleware that sits in front of **any** MCP server, either OpenGxP's own tools or any other server
through a proxy. The agent sees the same tools; every call passes through four controls.

```
Agent (Claude / Copilot / ...) ──MCP──▶ GxP Gateway ──MCP──▶ target server(s)
                                          │ 1 identify actor (token claims / configured local user)
                                          │ 2 policy: role allow/deny, validated-only, approval needed?
                                          │ 3 e-signature approval (single-use, bound to tool + exact inputs)
                                          │ 4 audit record (hash-chained, append-only)
                                          ▼
                              audit.jsonl · approvals/ · signers.json · tool registry
```

## Components (LLD)
| Module | Responsibility |
|---|---|
| `gateway/audit.py` | Append-only JSONL audit trail. Each record has `seq`, a UTC timestamp, `prev_hash` and `hash = sha256(prev_hash + canonical record)`. File lock and fsync on every write. `verify()` recomputes the chain and reports the first broken record (edited, deleted or reordered). |
| `gateway/identity.py` | Actor = user ID + role + auth method. From MCP auth token claims (`sub`, `roles`) on HTTP; from `OPENGXP_USER` / `OPENGXP_ROLE` for local stdio use (recorded as `local-config`, so a reviewer sees the weaker assurance). |
| `gateway/policy.py` | YAML/JSON policy: `mode` (enforce / monitor), `require_identity`, role allow/deny globs, approval rules (globs plus "any tool not marked read-only"), `validated_only` roles, argument redaction globs, approval TTL and separate signer. `decide()` returns allow / deny / needs-approval with a reason. |
| `gateway/approvals.py` | Approval request → electronic signature → single-use consumption. A signature needs a registered signer, a password check (PBKDF2), a meaning (approved / rejected), a reason and a timestamp (Part 11 §11.50, §11.200 two-component ID + password). The approval is bound to the requester, tool and SHA-256 of the exact arguments; it expires and cannot be reused. |
| `gateway/registry.py` | Tool validation registry: intended use, risk (high / medium / low), status (validated / in_validation / not_validated), evidence (test IDs), version, validated on and by. Seeded from tool metadata (`validated_against`) and overridden by a registry file. |
| `gateway/middleware.py` | `GxPGatewayMiddleware`. `on_list_tools` hides denied tools and annotates the rest with `gxp` metadata. `on_call_tool` applies the controls in order and writes exactly one record per outcome (`executed` / `error` / `denied` / `approval_required`). |
| `gateway/reports.py` | Audit-trail review report (filters by date, user and tool; Markdown or CSV), including the chain verification result and the last hash. Also a CSA-style validation summary per tool: intended use → risk → evidence → test results (pytest JUnit XML) → conclusion. |
| `gateway/cli.py` | `opengxp-gateway serve` (proxy any MCP server, or OpenGxP itself), `verify`, `report`, `approvals`, `sign`, `add-signer`, `validation-summary`. |

## Deliberate limits (stated in every report)
- The gateway is a **control layer, not a validated system out of the box**. Each company still validates it for its intended use.
- Local stdio identity comes from configuration, not authentication. Use HTTP with OAuth for attributable multi-user use.
- Hash chaining detects tampering; it does not prevent deletion of the whole file. Ship records to WORM / backup storage for retention.
- In this design the signer authenticates through the gateway CLI, which is where the human signs. The agent never holds signer credentials.
