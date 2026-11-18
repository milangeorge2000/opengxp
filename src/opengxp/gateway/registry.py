"""Tool validation registry: the company's record of each tool's intended use, risk and validation status.

Status 'validated' comes only from the registry file (a company decision, with evidence and approver).
Tool metadata that claims tests (e.g. OpenGxP's 'validated_against') is shown as 'self_declared' evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Status = Literal["validated", "in_validation", "self_declared", "not_validated"]


class ToolRecord(BaseModel):
    tool: str
    intended_use: str | None = None
    risk: Literal["high", "medium", "low"] | None = None
    status: Status = "not_validated"
    version: str | None = None
    evidence: list[str] = Field(default_factory=list, description="Test IDs / documents; '::'-style pytest node globs allowed.")
    validated_on: str | None = None
    validated_by: str | None = None
    vendor_claim: str | None = Field(None, description="What the tool itself declares (metadata), not a company decision.")


class Registry:
    def __init__(self, path: str | Path | None = None):
        self.records: dict[str, ToolRecord] = {}
        if path and Path(path).exists():
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            for name, rec in (data.get("tools") or {}).items():
                self.records[name] = ToolRecord(tool=name, **rec)

    def record_for(self, name: str, meta: dict | None = None, version: str | None = None) -> ToolRecord:
        claim = None
        if meta:
            v = meta.get("validated_against")
            claim = v if isinstance(v, str) else (", ".join(v) if v else None)
        rec = self.records.get(name)
        if rec:
            out = rec.model_copy()
            out.vendor_claim = out.vendor_claim or claim
            # a validation is for a specific version; a different running version is not validated
            if out.status == "validated" and out.version and version and str(out.version) != str(version):
                out.status = "not_validated"
                out.intended_use = (out.intended_use or "") + f" [registry validated v{rec.version}, running v{version}]"
            return out
        return ToolRecord(tool=name, status="self_declared" if claim else "not_validated", vendor_claim=claim,
                          version=str(version) if version else None)
