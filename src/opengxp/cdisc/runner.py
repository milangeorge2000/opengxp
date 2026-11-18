"""Run the official CDISC CORE command-line tool (cdisc-rules-engine) as a subprocess.

OpenGxP does not reimplement CORE: it calls the CLI that CDISC publishes (release binary or a clone
of github.com/cdisc-org/cdisc-rules-engine) and reads its JSON report. Point OPENGXP_CORE at either
  * the `core` executable from a CORE release (unzipped folder), or
  * `core.py` in a clone of the repository (run with this Python, which needs `opengxp[cdisc]`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from opengxp.core import GxPInputError

SETUP_HELP = (
    "CDISC CORE is not configured. Install it, then set OPENGXP_CORE:\n"
    "  1. Download the CORE release for your OS from https://github.com/cdisc-org/cdisc-rules-engine/releases "
    "and unzip it; set OPENGXP_CORE=/path/to/core/core (the executable).\n"
    "     or clone the repository and set OPENGXP_CORE=/path/to/cdisc-rules-engine/core.py "
    "(install with `pip install opengxp[cdisc]`).\n"
    "  2. The release ships a rules cache. To refresh it you need a free CDISC Library API key: "
    "`core update-cache --apikey <key>` (or CDISC_LIBRARY_API_KEY)."
)


@dataclass
class CoreCommand:
    argv: list[str]
    cwd: Path


def locate_core() -> CoreCommand:
    raw = os.environ.get("OPENGXP_CORE")
    if not raw:
        raise GxPInputError(SETUP_HELP)
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        raise GxPInputError(f"OPENGXP_CORE points to {p}, which does not exist.\n" + SETUP_HELP)
    if p.suffix == ".py":
        return CoreCommand([sys.executable, str(p)], p.parent)
    return CoreCommand([str(p)], p.parent)


def run_core(args: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    cmd = locate_core()
    try:
        return subprocess.run(cmd.argv + args, cwd=cmd.cwd, capture_output=True, text=True, timeout=timeout_s,
                              check=False)
    except subprocess.TimeoutExpired as e:
        raise GxPInputError(f"CORE did not finish within {timeout_s:g} s. Validate fewer datasets or specific "
                            "rule_ids, or raise timeout_s.") from e


def core_version(timeout_s: float = 60) -> str | None:
    cp = run_core(["version"], timeout_s)
    return cp.stdout.strip().splitlines()[-1] if cp.returncode == 0 and cp.stdout.strip() else None


def validate(dataset_paths: list[Path], standard: str, version: str, *, define_xml: Path | None = None,
             ct_packages: list[str] | None = None, rule_ids: list[str] | None = None,
             timeout_s: float = 1800) -> dict:
    """Run `core validate` and return the parsed JSON report."""
    with tempfile.TemporaryDirectory(prefix="opengxp-core-") as tmp:
        out = Path(tmp) / "report.json"
        args = ["validate", "-s", standard, "-v", version, "-of", "JSON", "-o", str(out)]
        for p in dataset_paths:
            args += ["-dp", str(p)]
        if define_xml:
            args += ["--define-xml-path", str(define_xml)]
        for ct in ct_packages or []:
            args += ["-ct", ct]
        for r in rule_ids or []:
            args += ["-r", r]
        cp = run_core(args, timeout_s)
        if not out.exists():
            tail = (cp.stderr or cp.stdout or "").strip().splitlines()[-15:]
            raise GxPInputError("CORE produced no report (exit code %s). Last output:\n%s"
                                % (cp.returncode, "\n".join(tail) or "(none)"))
        return json.loads(out.read_text(encoding="utf-8"))


def list_rules(rule_ids: list[str], timeout_s: float = 120) -> list[dict]:
    args = ["list-rules"]
    for r in rule_ids:
        args += ["-r", r]
    cp = run_core(args, timeout_s)
    if cp.returncode != 0:
        raise GxPInputError("CORE list-rules failed: " + (cp.stderr or cp.stdout).strip()[-500:])
    start = cp.stdout.find("[")
    return json.loads(cp.stdout[start:]) if start >= 0 else []
