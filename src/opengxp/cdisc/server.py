"""MCP server: CDISC conformance via the open CDISC CORE engine (SDTM, SEND, ADaM)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from opengxp.cdisc import report as rep
from opengxp.cdisc import runner
from opengxp.core import GxPInputError

sdtm = FastMCP(
    "opengxp-cdisc",
    instructions=(
        "CDISC conformance checks with the open CDISC CORE engine. Typical loop: sdtm_validate -> read the "
        "largest rule groups -> sdtm_explain_rule -> fix the mapping/data -> sdtm_validate again with rule_ids "
        "-> sdtm_diff_runs; stop when clean or when progress is false twice. Call sdtm_core_status first if "
        "unsure CORE is installed."
    ),
    mask_error_details=True,
)

DATA_EXT = {".xpt", ".json", ".ndjson", ".xlsx", ".xml"}
_LOCAL = {"readOnlyHint": True, "idempotentHint": False, "destructiveHint": False, "openWorldHint": False}


def _dataset_files(path: str) -> list[Path]:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise GxPInputError(f"datasets_path {p} does not exist.")
    files = [p] if p.is_file() else sorted(f for f in p.iterdir() if f.suffix.lower() in DATA_EXT and not f.name.startswith("."))
    files = [f for f in files if f.name.lower() != "define.xml"]
    if not files:
        raise GxPInputError(f"No dataset files ({', '.join(sorted(DATA_EXT))}) in {p}.")
    return files


def _version(v: str) -> str:
    v = v.strip().replace(".", "-")
    if not re.fullmatch(r"\d+(-\d+)*", v):
        raise GxPInputError(f"version '{v}' should look like '3-4' or '3.4'.")
    return v


class CoreStatus(BaseModel):
    configured: bool
    core_path: str | None
    core_version: str | None
    runs_dir: str
    setup: str | None


@sdtm.tool(title="CDISC CORE setup status", tags={"cdisc"}, annotations=_LOCAL | {"idempotentHint": True}, timeout=90)
def sdtm_core_status() -> CoreStatus:
    """Is CDISC CORE installed and which version? Returns setup instructions when it is not."""
    import os
    try:
        ver = runner.core_version()
        return CoreStatus(configured=True, core_path=os.environ.get("OPENGXP_CORE"), core_version=ver,
                          runs_dir=str(rep.runs_dir()), setup=None)
    except GxPInputError as e:
        return CoreStatus(configured=False, core_path=os.environ.get("OPENGXP_CORE"), core_version=None,
                          runs_dir=str(rep.runs_dir()), setup=str(e))


@sdtm.tool(title="Validate datasets with CDISC CORE", tags={"cdisc", "sdtm", "send", "adam"}, annotations=_LOCAL,
           meta={"engine": "CDISC CORE (cdisc-rules-engine, MIT)"}, timeout=3600)
async def sdtm_validate(
    datasets_path: Annotated[str, Field(description="Folder of datasets (.xpt, Dataset-JSON .json/.ndjson, .xlsx) or one file.")],
    standard: Annotated[Literal["sdtmig", "sendig", "adamig"], Field(description="CDISC standard.")] = "sdtmig",
    version: Annotated[str, Field(description="Standard version, e.g. '3-4' (SDTMIG 3.4) or '3.1.1'.")] = "3-4",
    define_xml_path: Annotated[str | None, Field(description="Define-XML, enables define-based rules.")] = None,
    ct_packages: Annotated[list[str] | None, Field(description="Controlled terminology packages, e.g. ['sdtmct-2024-09-27'].")] = None,
    rule_ids: Annotated[list[str] | None, Field(description="Run only these CORE rules (fast re-check after a fix).")] = None,
    domains: Annotated[list[str] | None, Field(description="Only report issues for these domains, e.g. ['AE', 'DM'].")] = None,
    group_by: Annotated[Literal["rule", "dataset", "none"], Field(description="'rule' (default) gives one line per rule with a count and examples.")] = "rule",
    examples_per_group: Annotated[int, Field(ge=0, le=20)] = 3,
    limit: Annotated[int, Field(ge=1, le=200, description="Groups per page.")] = 25,
    timeout_s: Annotated[float, Field(gt=0, le=3600)] = 1800,
    ctx: Context | None = None,
) -> rep.ValidationResult:
    """Run CDISC CORE conformance rules on SDTM/SEND/ADaM datasets and return issues grouped by rule, with
    counts, CDISC/FDA rule IDs and a few example records. The full report is stored; page through it with
    sdtm_get_run and compare runs with sdtm_diff_runs."""
    files = _dataset_files(datasets_path)
    ver = _version(version)
    define = Path(define_xml_path).expanduser().resolve() if define_xml_path else None
    if define and not define.exists():
        raise GxPInputError(f"define_xml_path {define} does not exist.")
    if ctx:
        await ctx.report_progress(progress=0, total=2)
    report = await asyncio.to_thread(runner.validate, files, standard, ver, define_xml=define,
                                     ct_packages=ct_packages, rule_ids=rule_ids, timeout_s=timeout_s)
    run_id = rep.save_run(report, {"standard": standard, "version": ver, "files": [str(f) for f in files],
                                   "rule_ids": rule_ids, "define": str(define) if define else None})
    if ctx:
        await ctx.report_progress(progress=2, total=2)
    return rep.summarise(report, run_id, standard, ver, group_by=group_by, domains=domains,
                         examples_per_group=examples_per_group, limit=limit)


@sdtm.tool(title="Page through a stored CORE run", tags={"cdisc"}, annotations=_LOCAL | {"idempotentHint": True}, timeout=30)
def sdtm_get_run(
    run_id: str,
    group_by: Literal["rule", "dataset", "none"] = "rule",
    domains: list[str] | None = None,
    rule_ids: list[str] | None = None,
    examples_per_group: Annotated[int, Field(ge=0, le=50)] = 3,
    limit: Annotated[int, Field(ge=1, le=200)] = 25,
    cursor: Annotated[int, Field(ge=0, description="next_cursor from the previous page.")] = 0,
) -> rep.ValidationResult:
    """Re-read a stored validation run with different grouping, filters or page, without re-running CORE."""
    run = rep.load_run(run_id)
    m = run["meta"]
    return rep.summarise(run["report"], run_id, m["standard"], m["version"], group_by=group_by, domains=domains,
                         rule_ids=rule_ids, examples_per_group=examples_per_group, limit=limit, cursor=cursor)


@sdtm.tool(title="Explain a CORE rule", tags={"cdisc"}, annotations=_LOCAL | {"idempotentHint": True}, timeout=120)
def sdtm_explain_rule(core_id: Annotated[str, Field(pattern=r"^CORE-\d{6}$", description="e.g. CORE-000001")]) -> rep.RuleExplanation:
    """What a CORE rule checks: description, message, CDISC/FDA rule references with IG sections, applicable
    domains/classes and the condensed check logic. Uses the CORE rules cache; falls back to stored runs."""
    try:
        rules = runner.list_rules([core_id])
        if rules:
            return rep.explain_from_rule(rules[0], "core_cache")
    except GxPInputError:
        pass
    found = rep.explain_from_runs(core_id)
    if found:
        return found
    raise GxPInputError(f"{core_id} not found in the CORE rules cache or any stored run. Check the ID, or call sdtm_core_status.")


@sdtm.tool(title="Compare two CORE runs", tags={"cdisc"}, annotations=_LOCAL | {"idempotentHint": True}, timeout=30)
def sdtm_diff_runs(before_run_id: str, after_run_id: str) -> rep.DiffResult:
    """Issues fixed, new and unchanged between two validation runs, per rule. 'progress' tells a fix loop
    whether it is getting anywhere; 'clean' says nothing is left."""
    return rep.diff_runs(before_run_id, after_run_id)


@sdtm.resource("cdisc://runs/{run_id}/report", mime_type="application/json", tags={"cdisc"},
               description="The full CORE JSON report of a stored run.")
def run_report(run_id: str) -> dict:
    return rep.load_run(run_id)["report"]


@sdtm.prompt(description="Self-correcting SDTM conformance loop using CORE.", tags={"cdisc"})
def sdtm_fix_loop(datasets_path: str, standard: str = "sdtmig", version: str = "3-4") -> str:
    return (
        f"Make the datasets in {datasets_path} conform to {standard} {version}.\n"
        "1. sdtm_validate. 2. For the three largest rule groups call sdtm_explain_rule and decide the fix in the "
        "mapping or derivation (never edit values just to silence a rule). 3. Apply fixes, re-run sdtm_validate "
        "with rule_ids for the rules you touched, then sdtm_diff_runs. 4. Repeat until clean, or stop and report "
        "if progress is false twice. Keep a change log: rule, dataset, what changed, why, run IDs before/after."
    )
