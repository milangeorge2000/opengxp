"""Turn a CDISC CORE JSON report into agent-sized results: issues grouped by rule with a few examples,
filters, pagination, stored runs, and run-to-run diffs (the stopping signal for a fix-and-revalidate loop)."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from opengxp.core import Citation, Finding, GxPInputError, GxPResult, provenance

CORE_URL = "https://github.com/cdisc-org/cdisc-rules-engine"
RULES_URL = "https://www.cdisc.org/standards/knowledge-base/conformance-rules"
GroupBy = Literal["rule", "dataset", "none"]


# ---- run store -------------------------------------------------------------------------------------
def runs_dir() -> Path:
    d = Path(os.environ.get("OPENGXP_DATA_DIR", Path.home() / ".opengxp")) / "core-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_run(report: dict, meta: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rid = f"{stamp}-{hashlib.sha256(json.dumps(meta, sort_keys=True, default=str).encode()).hexdigest()[:6]}-{uuid.uuid4().hex[:6]}"
    (runs_dir() / f"{rid}.json").write_text(json.dumps({"run_id": rid, "meta": meta, "report": report}, default=str))
    return rid


def load_run(run_id: str) -> dict:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise GxPInputError(f"Invalid run_id '{run_id}'.")
    p = runs_dir() / f"{run_id}.json"
    if not p.exists():
        known = sorted(x.stem for x in runs_dir().glob("*.json"))[-5:]
        raise GxPInputError(f"Unknown run_id '{run_id}'. Recent runs: {', '.join(known) or 'none'}.")
    return json.loads(p.read_text())


# ---- models ----------------------------------------------------------------------------------------
class IssueExample(BaseModel):
    dataset: str | None
    usubjid: str | None = None
    row: str | None = None
    seq: str | None = None
    variables: list[str] = []
    values: list[Any] = []


class IssueGroup(BaseModel):
    key: str = Field(description="CORE rule ID, or dataset name when grouped by dataset.")
    core_ids: list[str]
    cdisc_rule_ids: list[str] = []
    fda_rule_ids: list[str] = []
    message: str
    datasets: list[str]
    count: int
    examples: list[IssueExample]


class DatasetInfo(BaseModel):
    name: str
    records: int | None = None


class ValidationTotals(BaseModel):
    issues: int
    rules_with_issues: int
    rules_run: int
    rules_skipped: int
    execution_errors: int


class ValidationResult(GxPResult):
    run_id: str
    standard: str
    version: str
    core_version: str | None
    datasets: list[DatasetInfo]
    totals: ValidationTotals
    group_by: GroupBy
    groups: list[IssueGroup]
    next_cursor: int | None = Field(None, description="Pass as cursor to get the next page of groups.")
    execution_errors: list[dict] = []


class RuleCount(BaseModel):
    core_id: str
    before: int
    after: int


class DiffResult(GxPResult):
    before_run: str
    after_run: str
    fixed: int
    new: int
    unchanged: int
    by_rule: list[RuleCount]
    rules_not_compared: list[str] = Field(default_factory=list, description="Rules run in only one of the two runs.")
    clean: bool = Field(description="True when the after-run has no issues.")
    progress: bool = Field(description="True when issues went down. Stop a fix loop when false twice running.")


class RuleExplanation(GxPResult):
    core_id: str
    description: str | None = None
    message: str | None = None
    rule_type: str | None = None
    sensitivity: str | None = None
    executability: str | None = None
    domains: Any = None
    classes: Any = None
    references: list[dict] = []
    check_logic: str | None = Field(None, description="Rule conditions as JSON (truncated).")
    source: Literal["core_cache", "previous_run"]


# ---- parsing ---------------------------------------------------------------------------------------
def _details(report: dict) -> list[dict]:
    return report.get("Issue_Details") or report.get("Issue Details") or []


def _rules(report: dict) -> list[dict]:
    return report.get("Rules_Report") or report.get("Rules Report") or []


def _issue_key(d: dict) -> tuple:
    return (d.get("core_id"), d.get("dataset"), str(d.get("USUBJID", "")), str(d.get("row", "")),
            str(d.get("SEQ", "")), tuple(d.get("variables") or []))


def _domain_of(dataset: str | None) -> str:
    return (dataset or "").split(".")[0].upper()[:4]


def _split_ids(rules: dict, ids: list[str], field: str) -> list[str]:
    """CORE joins several rule IDs into one string ('CG0082, TIG0347'); return them separately."""
    out = set()
    for i in ids:
        for part in str((rules.get(i) or {}).get(field) or "").split(","):
            if part.strip():
                out.add(part.strip())
    return sorted(out)


def _ex(d: dict) -> IssueExample:
    vals = d.get("values")
    return IssueExample(dataset=d.get("dataset"), usubjid=str(d.get("USUBJID") or "") or None,
                        row=str(d.get("row") or "") or None, seq=str(d.get("SEQ") or "") or None,
                        variables=list(d.get("variables") or []),
                        values=vals if isinstance(vals, list) else ([] if vals in (None, "") else [vals]))


def summarise(report: dict, run_id: str, standard: str, version: str, *, group_by: GroupBy = "rule",
              domains: list[str] | None = None, rule_ids: list[str] | None = None, examples_per_group: int = 3,
              limit: int = 25, cursor: int = 0) -> ValidationResult:
    rules = {r.get("core_id"): r for r in _rules(report)}
    # CORE writes rule execution errors into Issue_Details with variables == "" and no record
    exec_errors = [d for d in _details(report) if d.get("variables") == "" and d.get("row", "") == ""]
    issues = [d for d in _details(report) if not (d.get("variables") == "" and d.get("row", "") == "")]
    if domains:
        want = {x.upper() for x in domains}
        issues = [d for d in issues if _domain_of(d.get("dataset")) in want]
    if rule_ids:
        issues = [d for d in issues if d.get("core_id") in set(rule_ids)]

    buckets: dict[str, list[dict]] = defaultdict(list)
    for d in issues:
        k = {"rule": d.get("core_id"), "dataset": d.get("dataset"), "none": f"{d.get('core_id')}|{d.get('dataset')}|{d.get('row')}"}[group_by]
        buckets[str(k)].append(d)
    groups = []
    for k, ds in buckets.items():
        ids = sorted({str(d.get("core_id")) for d in ds})
        groups.append(IssueGroup(
            key=k, core_ids=ids,
            cdisc_rule_ids=_split_ids(rules, ids, "cdisc_rule_id"),
            fda_rule_ids=_split_ids(rules, ids, "fda_rule_id"),
            message=ds[0].get("message") or "", datasets=sorted({str(d.get("dataset")) for d in ds}),
            count=len(ds), examples=[_ex(d) for d in ds[:examples_per_group]]))
    groups.sort(key=lambda g: (-g.count, g.key))
    page = groups[cursor:cursor + limit]
    nxt = cursor + limit if cursor + limit < len(groups) else None

    statuses = [str(r.get("status", "")).upper() for r in rules.values()]
    totals = ValidationTotals(
        issues=len(issues), rules_with_issues=len({d.get("core_id") for d in issues}), rules_run=len(rules),
        rules_skipped=sum("SKIP" in s for s in statuses), execution_errors=sum("ERROR" in s for s in statuses))
    conf = report.get("Conformance_Details") or {}
    ds_info = [DatasetInfo(name=str(d.get("filename") or d.get("name")), records=d.get("length"))
               for d in (report.get("Dataset_Details") or [])]

    findings = []
    if totals.issues:
        findings.append(Finding(level="action_required", code="CONFORMANCE_ISSUES",
                                message=f"{totals.issues} issues across {totals.rules_with_issues} rules."))
    if totals.execution_errors:
        findings.append(Finding(level="warning", code="RULE_EXECUTION_ERRORS",
                                message=f"{totals.execution_errors} rules failed to execute; their checks did not run."))
    if totals.rules_skipped:
        findings.append(Finding(level="info", code="RULES_SKIPPED",
                                message=f"{totals.rules_skipped} rules were skipped (not applicable to these datasets)."))
    top = ", ".join(f"{g.key} ({g.count})" for g in groups[:3])
    return ValidationResult(
        summary=(f"CORE {standard} {version}: {totals.issues} issues in {totals.rules_with_issues} rules "
                 f"({totals.rules_run} rules run)." + (f" Largest: {top}." if top else " No issues reported.")),
        findings=findings,
        assumptions=["Results are CDISC CORE's; OpenGxP only groups, filters and pages them.",
                     "Domain filter matches the first 2-4 letters of the dataset file name."],
        citations=[Citation(guideline=f"CDISC CORE rules for {standard.upper()} {version}", section="Rules_Report",
                            rule="Each issue cites its CORE ID and, where defined, CDISC / FDA rule IDs.", url=RULES_URL),
                   Citation(guideline="CDISC CORE engine", section=f"version {conf.get('CORE_Engine_Version', 'unknown')}",
                            rule="Open-source conformance engine (MIT).", url=CORE_URL)],
        provenance=provenance("sdtm_validate", f"{standard} {version}", dict(run=run_id, group=group_by, d=domains,
                              r=rule_ids, lim=limit, cur=cursor), ["CORE JSON report format v1.0"]),
        run_id=run_id, standard=standard, version=version, core_version=conf.get("CORE_Engine_Version"),
        datasets=ds_info, totals=totals, group_by=group_by, groups=page, next_cursor=nxt,
        execution_errors=exec_errors[:20],
    )


def diff_runs(before_id: str, after_id: str) -> DiffResult:
    b, a = load_run(before_id)["report"], load_run(after_id)["report"]
    # Only rules executed in BOTH runs can be compared; a rule-subset re-run must not count as "fixed".
    ran_b = {r.get("core_id") for r in _rules(b)}
    ran_a = {r.get("core_id") for r in _rules(a)}
    both = ran_b & ran_a if ran_b and ran_a else ran_b | ran_a | {d.get("core_id") for d in _details(b) + _details(a)}
    not_compared = sorted(str(x) for x in (ran_b ^ ran_a) if x)
    kb = {_issue_key(d) for d in _details(b) if d.get("core_id") in both}
    ka = {_issue_key(d) for d in _details(a) if d.get("core_id") in both}
    cb, ca = defaultdict(int), defaultdict(int)
    for k in kb:
        cb[k[0]] += 1
    for k in ka:
        ca[k[0]] += 1
    by_rule = sorted((RuleCount(core_id=str(r), before=cb[r], after=ca[r]) for r in set(cb) | set(ca)),
                     key=lambda x: (x.after - x.before, x.core_id))
    fixed, new = len(kb - ka), len(ka - kb)
    return DiffResult(
        summary=(f"{fixed} issues fixed, {new} new, {len(ka & kb)} unchanged; {len(ka)} remain"
                 f" across {len(both)} rules run in both" + (f" ({len(not_compared)} rules ran in only one run, not compared)." if not_compared else ".")),
        findings=([Finding(level="warning", code="NEW_ISSUES", message=f"{new} issues appeared that were not in the earlier run.")] if new else [])
                 + ([Finding(level="info", code="RULES_NOT_COMPARED",
                             message=f"{len(not_compared)} rules ran in only one of the runs (e.g. a rule_ids re-check) and are excluded.")]
                    if not_compared else []),
        citations=[Citation(guideline="CDISC CORE", section="Issue_Details", rule="Issue identity = rule, dataset, subject, record, variables.", url=CORE_URL)],
        provenance=provenance("sdtm_diff_runs", "CORE", dict(b=before_id, a=after_id), ["set-difference on issue identity"]),
        before_run=before_id, after_run=after_id, fixed=fixed, new=new, unchanged=len(ka & kb), by_rule=by_rule,
        rules_not_compared=not_compared,
        clean=not _details(a), progress=len(ka) < len(kb),
    )


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, "", [], {}):
            return d[k]
    return None


def explain_from_rule(rule: dict, source: Literal["core_cache", "previous_run"]) -> RuleExplanation:
    msg = _first(rule, "message")
    acts = rule.get("actions") or []
    if not msg and acts and isinstance(acts, list):
        msg = (acts[0].get("params") or {}).get("message")
    if not msg and isinstance(rule.get("outcome"), dict):
        msg = rule["outcome"].get("message")
    refs = []
    for auth in rule.get("authorities") or []:
        for std in auth.get("Standards") or auth.get("standards") or []:
            for ref in std.get("References") or std.get("references") or []:
                rid = (ref.get("Rule_Identifier") or {}).get("Id")
                for c in ref.get("Citations") or [{}]:
                    refs.append({"organization": auth.get("Organization"), "standard": std.get("Name"),
                                 "version": std.get("Version"), "rule_id": rid, "document": c.get("Document"),
                                 "section": c.get("Section"), "guidance": c.get("Cited_Guidance")})
    logic = rule.get("conditions") or rule.get("check")
    cid = str(_first(rule, "core_id", "core-id") or "?")
    return RuleExplanation(
        summary=f"{cid}: {msg or _first(rule, 'description') or 'no message'}",
        citations=[Citation(guideline=f"{r['organization']} {r['standard']} {r['version']}", section=r.get("section") or "",
                            rule=r.get("guidance") or r.get("rule_id"), url=RULES_URL) for r in refs[:5]]
                  or [Citation(guideline="CDISC CORE rules", section=cid, url=RULES_URL)],
        provenance=provenance("sdtm_explain_rule", "CORE", {"id": cid, "src": source}, []),
        core_id=cid, description=_first(rule, "description", "Description"), message=msg,
        rule_type=_first(rule, "rule_type", "Rule_Type"), sensitivity=_first(rule, "sensitivity", "Sensitivity"),
        executability=_first(rule, "executability", "Executability"), domains=rule.get("domains"),
        classes=rule.get("classes"), references=refs,
        check_logic=json.dumps(logic, default=str)[:2000] if logic else None, source=source,
    )


def explain_from_runs(core_id: str) -> RuleExplanation | None:
    for p in sorted(runs_dir().glob("*.json"), reverse=True):
        rep = json.loads(p.read_text())["report"]
        for r in _rules(rep):
            if r.get("core_id") == core_id:
                return explain_from_rule({"core_id": core_id, "message": r.get("message"),
                                          "description": f"CDISC rule {r.get('cdisc_rule_id') or '-'}; FDA rule {r.get('fda_rule_id') or '-'}"},
                                         "previous_run")
    return None
