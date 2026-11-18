"""Audit-trail review report and CSA-style validation summary."""

from __future__ import annotations

import csv
import fnmatch
import io
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from opengxp.gateway.audit import AuditLog, utc_now
from opengxp.gateway.registry import Registry, ToolRecord

LIMITS = ("This report is produced by the OpenGxP gateway, a control layer that the user organisation must "
          "validate for its intended use. Hash-chain verification detects edits, deletions and reordering "
          "within the file; it does not replace secure, backed-up retention.")


def _rows(log: AuditLog, since: str | None, until: str | None, user: str | None, tool: str | None) -> list[dict]:
    out = []
    for r in log:
        ts = r.get("timestamp", "")
        if since and ts < since:
            continue
        if until and ts > until:
            continue
        if user and (r.get("actor") or {}).get("user_id") != user:
            continue
        if tool and not fnmatch.fnmatchcase(r.get("tool", ""), tool):
            continue
        out.append(r)
    return out


def audit_report(log: AuditLog, *, since: str | None = None, until: str | None = None, user: str | None = None,
                 tool: str | None = None, fmt: str = "md") -> str:
    v = log.verify()
    rows = _rows(log, since, until, user, tool)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["seq", "timestamp_utc", "event", "user_id", "role", "auth_method", "agent", "tool", "tool_version",
                    "validation_status", "approval_id", "signer", "meaning", "inputs_sha256", "outputs_sha256", "reason_or_error", "hash"])
        for r in rows:
            a, s = r.get("actor") or {}, r.get("signature") or {}
            w.writerow([r["seq"], r["timestamp"], r.get("event"), a.get("user_id"), a.get("role"), a.get("auth_method"),
                        (r.get("agent") or {}).get("name"), r.get("tool"), r.get("tool_version"), r.get("validation_status"),
                        r.get("approval_id"), s.get("printed_name"), s.get("meaning"), r.get("inputs_sha256"),
                        r.get("outputs_sha256"), r.get("reason") or r.get("error"), r["hash"]])
        return buf.getvalue()

    ev = Counter(r.get("event") for r in rows)
    lines = [
        "# Audit trail review report", "",
        f"- Generated: {utc_now()} (UTC)",
        f"- Audit file: `{log.path}`",
        f"- Filters: since={since or '-'}, until={until or '-'}, user={user or '-'}, tool={tool or '-'}",
        f"- **Chain verification: {'PASSED' if v.ok else 'FAILED'}** ({v.records} records; last hash `{v.last_hash[:16]}…`)"
        + ("" if v.ok else f" - first bad record seq {v.first_bad_seq}: {v.reason}"),
        f"- Records in scope: {len(rows)} | " + ", ".join(f"{k}: {n}" for k, n in sorted(ev.items())), "",
        "| Seq | Time (UTC) | Event | User (auth) | Agent | Tool (version) | Validation | Approval / signature | Detail |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        a, s = r.get("actor") or {}, r.get("signature") or {}
        sig = f"{r.get('approval_id') or ''} {s.get('printed_name', '')} {s.get('meaning', '')}".strip()
        detail = r.get("reason") or r.get("error") or (r.get("output_summary") or "")[:80]
        lines.append(f"| {r['seq']} | {r['timestamp']} | {r.get('event')} | {a.get('user_id')} ({a.get('auth_method')}) | "
                     f"{(r.get('agent') or {}).get('name') or '-'} | {r.get('tool')} ({r.get('tool_version') or '-'}) | "
                     f"{r.get('validation_status')} | {sig or '-'} | {str(detail).replace('|', '/')} |")
    lines += ["", "Reviewed by: ____________________  Date: ____________  Signature: ____________", "", f"_{LIMITS}_"]
    return "\n".join(lines)


def _junit(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    res = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        node = f"{tc.get('classname', '').replace('.', '/')}.py::{tc.get('name')}"
        status = "failed" if tc.find("failure") is not None or tc.find("error") is not None else (
            "skipped" if tc.find("skipped") is not None else "passed")
        res[node] = status
    return res


def validation_summary(registry: Registry, tools: list[tuple[str, dict | None, str | None]],
                       junit_xml: str | Path | None = None) -> str:
    results = _junit(junit_xml)
    lines = ["# Tool validation summary (CSA-style record)", "", f"Generated {utc_now()} (UTC).", ""]
    for name, meta, version in tools:
        rec: ToolRecord = registry.record_for(name, meta, version)
        matched = {n: s for n, s in results.items() for g in rec.evidence if fnmatch.fnmatchcase(n, g)}
        c = Counter(matched.values())
        concl = ("Validated for the stated intended use." if rec.status == "validated" and c.get("failed", 0) == 0 and matched
                 else "NOT validated: tests failing." if c.get("failed") else
                 "NOT validated by the organisation." if rec.status != "validated" else "Validated (no linked test results supplied).")
        lines += [f"## {name} (version {version or '-'})",
                  f"- Intended use: {rec.intended_use or '_not documented_'}",
                  f"- Risk: {rec.risk or '_not assessed_'}",
                  f"- Status: **{rec.status}**" + (f" (by {rec.validated_by} on {rec.validated_on})" if rec.validated_by else ""),
                  f"- Vendor / tool claim: {rec.vendor_claim or '-'}",
                  f"- Evidence: {', '.join(rec.evidence) or '-'}",
                  f"- Test results: {c.get('passed', 0)} passed, {c.get('failed', 0)} failed, {c.get('skipped', 0)} skipped"
                  + ("" if results else " (no JUnit file supplied)"),
                  f"- Conclusion: {concl}", ""]
    lines.append(f"_{LIMITS}_")
    return "\n".join(lines)
