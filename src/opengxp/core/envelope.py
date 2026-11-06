"""Shared result envelope. Every OpenGxP tool returns a subclass of GxPResult so an agent
always gets: the answer, which clause it came from, which guideline version, what was assumed,
and what a human should check."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from opengxp import __version__

DISCLAIMER = "Decision support only. Verify against the current guideline and your site SOPs."


class Citation(BaseModel):
    guideline: str = Field(description="Guideline and revision, e.g. 'ICH Q3A(R2)'.")
    section: str = Field(description="Section, table or attachment applied.")
    rule: str | None = Field(None, description="Short paraphrase of the rule applied (not verbatim).")
    url: str | None = Field(None, description="Official source document.")


class Finding(BaseModel):
    level: Literal["info", "warning", "action_required"]
    code: str = Field(description="Stable machine-readable code, e.g. 'ABOVE_QUALIFICATION'.")
    message: str


class Provenance(BaseModel):
    tool: str
    tool_version: str = __version__
    guideline_version: str
    inputs_hash: str = Field(description="sha256 of the canonical inputs; same hash means same answer.")
    computed_at: datetime
    validated_against: list[str] = Field(
        default_factory=list, description="Worked-example test sets this code path passes."
    )


class GxPResult(BaseModel):
    summary: str = Field(description="One or two sentence plain-language answer.")
    findings: list[Finding] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    citations: list[Citation]
    provenance: Provenance
    disclaimer: str = DISCLAIMER


def inputs_hash(inputs: dict[str, Any]) -> str:
    canon = json.dumps(inputs, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def provenance(tool: str, guideline_version: str, inputs: dict[str, Any], validated: list[str]) -> Provenance:
    return Provenance(
        tool=tool,
        guideline_version=guideline_version,
        inputs_hash=inputs_hash(inputs),
        computed_at=datetime.now(timezone.utc),
        validated_against=validated,
    )
