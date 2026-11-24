"""GxP gateway middleware: identity -> policy -> e-signature approval -> audit, around every tool call."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from opengxp.gateway.approvals import ApprovalStore
from opengxp.gateway.audit import AuditLog, canonical, sha256
from opengxp.gateway.policy import Policy, decide_roles, redact
from opengxp.gateway.registry import Registry


@dataclass
class Actor:
    user_id: str | None
    role: str | None
    auth_method: str
    roles: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.roles and self.role:
            self.roles = (self.role,)


def resolve_actor(fctx: Any) -> Actor:
    try:
        from fastmcp.server.dependencies import get_access_token
        tok = get_access_token()
    except Exception:
        tok = None
    if tok is not None:
        claims = getattr(tok, "claims", None) or {}
        # 'roles' (list) is set by the OpenGxP Supabase access-token hook from ref.user_roles. A user may hold
        # several roles; a tool is allowed if any of them allows it.
        roles = claims.get("roles") or claims.get("role") or []
        roles = tuple(r for r in (roles if isinstance(roles, list) else [roles]) if isinstance(r, str) and r)
        return Actor(claims.get("sub") or getattr(tok, "client_id", None), roles[0] if roles else None,
                     "oauth-token", roles)
    user = os.environ.get("OPENGXP_USER")
    return Actor(user, os.environ.get("OPENGXP_ROLE"), "local-config" if user else "none")


def _client(fctx: Any) -> dict:
    try:
        cp = fctx.session.client_params
        info = getattr(cp, "client_info", None) or getattr(cp, "clientInfo")  # MCP SDK v2 / v1 names
        return {"name": info.name, "version": info.version, "session_id": fctx.session_id}
    except Exception:
        return {"name": None, "version": None, "session_id": getattr(fctx, "session_id", None) if fctx else None}


def _read_only(tool: Any) -> bool:
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return False
    for attr in ("read_only_hint", "readOnlyHint"):
        try:
            v = getattr(ann, attr)
            if v is not None:
                return bool(v)
        except Exception:
            continue
    return False


def _result_digest(result: Any) -> tuple[str, str | None]:
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        payload: Any = sc
    else:
        payload = [c.model_dump() if hasattr(c, "model_dump") else str(c) for c in (getattr(result, "content", None) or [])]
    summary = sc.get("summary") if isinstance(sc, dict) else None
    return sha256(canonical(payload)), (summary[:500] if isinstance(summary, str) else None)


class GxPGatewayMiddleware(Middleware):
    def __init__(self, policy: Policy, audit: AuditLog, approvals: ApprovalStore, registry: Registry,
                 system_name: str = "opengxp-gateway"):
        self.policy, self.audit, self.approvals, self.registry = policy, audit, approvals, registry
        self.system = system_name

    async def _tool(self, fctx: Any, name: str) -> Any:
        try:
            return await fctx.fastmcp.get_tool(name)
        except Exception:
            return None

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        actor = resolve_actor(context.fastmcp_context)
        out = []
        for t in tools:
            rec = self.registry.record_for(t.name, t.meta, t.version)
            d = decide_roles(self.policy, actor.roles, bool(actor.user_id), t.name, _read_only(t), rec.status == "validated")
            if not d.allowed and self.policy.mode == "enforce":
                continue
            meta = dict(t.meta or {})
            meta["gxp"] = {"validation_status": rec.status, "needs_approval": d.needs_approval, "audited": True}
            out.append(t.model_copy(update={"meta": meta}))
        return out

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        fctx = context.fastmcp_context
        name = context.message.name
        args = dict(context.message.arguments or {})
        tool = await self._tool(fctx, name)
        version = str(tool.version) if tool is not None and tool.version is not None else None
        rec = self.registry.record_for(name, getattr(tool, "meta", None), version)
        actor = resolve_actor(fctx)
        args_hash = sha256(canonical(args))
        base = {
            "system": self.system,
            "actor": {"user_id": actor.user_id, "role": actor.role, "roles": list(actor.roles), "auth_method": actor.auth_method},
            "agent": _client(fctx),
            "tool": name, "tool_version": version, "validation_status": rec.status,
            "inputs": redact(self.policy, args) if self.policy.store_inputs else None,
            "inputs_sha256": args_hash,
        }
        d = decide_roles(self.policy, actor.roles, bool(actor.user_id), name, _read_only(tool), rec.status == "validated")
        enforce = self.policy.mode == "enforce"

        if not d.allowed:
            self.audit.append({**base, "event": "denied" if enforce else "would_deny", "reason": d.reason})
            if enforce:
                raise ToolError(f"Denied by GxP gateway policy: {d.reason}")

        approval = None
        if d.allowed and d.needs_approval:
            approval = self.approvals.consume(actor.user_id or "anonymous", name, args_hash)
            if approval is None:
                req = self.approvals.request(actor.user_id or "anonymous", name, args_hash, canonical(redact(self.policy, args)),
                                             self.policy.approval.ttl_minutes)
                self.audit.append({**base, "event": "approval_required" if enforce else "would_require_approval",
                                   "approval_id": req.id})
                if enforce:
                    raise ToolError(
                        f"Approval required ({req.id}). A registered approver must sign it, e.g. "
                        f"`opengxp-gateway sign {req.id} --meaning approved --reason \"...\"`. Then call {name} again with "
                        f"exactly the same arguments within {self.policy.approval.ttl_minutes} minutes.")

        t0 = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as e:
            self.audit.append({**base, "event": "error", "error": str(e)[:500],
                               "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                               "approval_id": approval.id if approval else None})
            raise
        out_hash, summary = _result_digest(result)
        self.audit.append({
            **base, "event": "executed", "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "outputs_sha256": out_hash, "output_summary": summary,
            "approval_id": approval.id if approval else None,
            "signature": approval.signature.model_dump() if approval and approval.signature else None,
        })
        return result
