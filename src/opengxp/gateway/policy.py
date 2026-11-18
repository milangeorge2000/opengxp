"""Access policy: who may call which tool, which calls need an electronic-signature approval,
whether only validated tools may be used, and which arguments are stored only as hashes."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class RoleRule(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"], description="Tool-name globs this role may call.")
    deny: list[str] = Field(default_factory=list)


class ApprovalRule(BaseModel):
    tools: list[str] = Field(default_factory=list, description="Tool-name globs that always need approval.")
    non_read_only: bool = Field(True, description="Any tool not annotated readOnlyHint=true needs approval.")
    ttl_minutes: int = 60
    separate_signer: bool = Field(True, description="Signer must be a different person from the requester (four eyes).")


class Policy(BaseModel):
    mode: Literal["enforce", "monitor"] = Field("enforce", description="monitor = log decisions but do not block.")
    require_identity: bool = True
    roles: dict[str, RoleRule] = Field(default_factory=lambda: {"default": RoleRule()})
    default_role: str = "default"
    approval: ApprovalRule = Field(default_factory=ApprovalRule)
    validated_only_roles: list[str] = Field(default_factory=list,
                                            description="Roles that may only call tools registered as validated.")
    redact_args: list[str] = Field(default_factory=list, description="Argument-name globs stored only as a hash.")
    store_inputs: bool = True

    @classmethod
    def load(cls, path: str | Path | None) -> "Policy":
        if not path:
            return cls()
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
        return cls(**(data or {}))


@dataclass
class Decision:
    allowed: bool
    needs_approval: bool
    reason: str


def _match(name: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


def decide(policy: Policy, role: str | None, identified: bool, tool: str, read_only: bool,
           validated: bool) -> Decision:
    if policy.require_identity and not identified:
        return Decision(False, False, "No identified user. Set up authentication (HTTP) or OPENGXP_USER (local).")
    rule = policy.roles.get(role or policy.default_role) or policy.roles.get(policy.default_role)
    if rule is None:
        return Decision(False, False, f"Role '{role}' is not defined in the policy.")
    if _match(tool, rule.deny) or not _match(tool, rule.allow):
        return Decision(False, False, f"Role '{role}' is not permitted to call {tool}.")
    if (role in policy.validated_only_roles) and not validated:
        return Decision(False, False, f"{tool} is not registered as validated; role '{role}' may only use validated tools.")
    needs = _match(tool, policy.approval.tools) or (policy.approval.non_read_only and not read_only)
    return Decision(True, needs, "approval required" if needs else "permitted")


def redact(policy: Policy, args: dict) -> dict:
    from opengxp.gateway.audit import canonical, sha256
    out = {}
    for k, v in (args or {}).items():
        out[k] = {"redacted_sha256": sha256(canonical(v))} if _match(k, policy.redact_args) else v
    return out
