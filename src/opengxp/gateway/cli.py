"""opengxp-gateway: run the GxP gateway in front of any MCP server, and manage audit, approvals, signers."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import shlex
import sys
from pathlib import Path

from fastmcp import FastMCP

from opengxp.gateway.approvals import ApprovalStore
from opengxp.gateway.audit import AuditLog
from opengxp.gateway.middleware import GxPGatewayMiddleware
from opengxp.gateway.policy import Policy
from opengxp.gateway.registry import Registry
from opengxp.gateway.reports import audit_report, validation_summary


def data_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("OPENGXP_GATEWAY_DIR") or Path.home() / ".opengxp" / "gateway").expanduser()


def build_gateway(target: str | FastMCP, *, policy: Policy, registry: Registry, root: Path,
                  system_name: str = "opengxp-gateway", auth=None) -> FastMCP:
    """Wrap a target with the GxP middleware. target: 'opengxp', a FastMCP server, a command line
    ('uvx some-mcp-server'), an MCP URL, or a path to an mcp.json config."""
    if isinstance(target, FastMCP):
        inner = target
    elif target == "opengxp":
        from opengxp.server import mcp as inner
    else:
        from fastmcp.server import create_proxy
        if target.startswith(("http://", "https://")) or target.endswith(".json"):
            spec = target
        else:
            parts = shlex.split(target)
            spec = {"mcpServers": {"target": {"command": parts[0], "args": parts[1:]}}}
        inner = create_proxy(spec)
    gw = FastMCP(system_name, instructions="All tool calls pass through a GxP gateway: role-based access, "
                 "electronic-signature approvals and a hash-chained audit trail.", auth=auth)
    gw.mount(inner)
    gw.add_middleware(GxPGatewayMiddleware(policy, AuditLog(root / "audit.jsonl"), ApprovalStore(root), registry,
                                           system_name))
    return gw


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="opengxp-gateway", description=__doc__)
    p.add_argument("--dir", help="Gateway data folder (default ~/.opengxp/gateway or $OPENGXP_GATEWAY_DIR)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run the gateway as an MCP server")
    s.add_argument("--target", default="opengxp", help="'opengxp', a command ('uvx foo-mcp'), an MCP URL, or mcp.json")
    s.add_argument("--policy"), s.add_argument("--registry")
    s.add_argument("--name", default="opengxp-gateway")
    s.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--auth", choices=["none", "supabase"], default="none",
                   help="HTTP only. supabase: users sign in with Supabase Auth (OAuth); their roles come from the "
                        "token's 'roles' claim and the same token is forwarded to the ref data service")
    s.add_argument("--public-url", help="Public base URL of this gateway (needed for --auth supabase)")

    sub.add_parser("verify", help="Verify the audit-trail hash chain")
    r = sub.add_parser("report", help="Audit-trail review report")
    r.add_argument("--since"), r.add_argument("--until"), r.add_argument("--user"), r.add_argument("--tool")
    r.add_argument("--format", choices=["md", "csv"], default="md")

    a = sub.add_parser("approvals", help="List approval requests")
    a.add_argument("--status", choices=["pending", "approved", "rejected", "consumed", "expired"])

    g = sub.add_parser("sign", help="Electronically sign (approve/reject) a request")
    g.add_argument("approval_id"), g.add_argument("--signer", required=True)
    g.add_argument("--meaning", choices=["approved", "rejected"], required=True)
    g.add_argument("--reason", required=True), g.add_argument("--policy")

    n = sub.add_parser("add-signer", help="Register an approver")
    n.add_argument("user_id"), n.add_argument("--name", required=True, help="Printed name shown on signatures")

    v = sub.add_parser("validation-summary", help="CSA-style validation summary for the target's tools")
    v.add_argument("--target", default="opengxp"), v.add_argument("--registry"), v.add_argument("--junit")

    args = p.parse_args(argv)
    root = data_dir(args.dir)
    root.mkdir(parents=True, exist_ok=True)

    if args.cmd == "serve":
        auth = None
        if args.auth == "supabase":
            if args.transport != "http" or not args.public_url:
                print("--auth supabase needs --transport http and --public-url", file=sys.stderr)
                return 2
            from fastmcp.server.auth.providers.supabase import SupabaseProvider

            from opengxp.ref.client import config as ref_config
            auth = SupabaseProvider(project_url=ref_config()[0], base_url=args.public_url)
        elif args.transport == "http":
            print("warning: HTTP without --auth: every caller is the OPENGXP_USER from the environment. "
                  "Use --auth supabase for attributable multi-user access.", file=sys.stderr)
        gw = build_gateway(args.target, policy=Policy.load(args.policy), registry=Registry(args.registry), root=root,
                           system_name=args.name, auth=auth)
        if args.transport == "http":
            gw.run(transport="http", port=args.port, show_banner=False)
        else:
            gw.run(show_banner=False)
    elif args.cmd == "verify":
        res = AuditLog(root / "audit.jsonl").verify()
        print(("PASSED" if res.ok else "FAILED") + f": {res.records} records, last hash {res.last_hash}"
              + ("" if res.ok else f"; first bad seq {res.first_bad_seq}: {res.reason}"))
        return 0 if res.ok else 1
    elif args.cmd == "report":
        print(audit_report(AuditLog(root / "audit.jsonl"), since=args.since, until=args.until, user=args.user,
                           tool=args.tool, fmt=args.format))
    elif args.cmd == "approvals":
        for ap in ApprovalStore(root).list(args.status):
            sig = f" signed by {ap.signature.printed_name} ({ap.signature.meaning})" if ap.signature else ""
            print(f"{ap.id}  {ap.status:9s} {ap.tool}  requested by {ap.requested_by} at {ap.requested_at}{sig}\n"
                  f"    inputs: {ap.args_preview}")
    elif args.cmd == "sign":
        store = ApprovalStore(root)
        ap = store.get(args.approval_id)
        print(f"Signing {ap.id}: {ap.tool} requested by {ap.requested_by}\n  inputs: {ap.args_preview}\n"
              f"  meaning: {args.meaning}; reason: {args.reason}")
        pw = getpass.getpass(f"Password for {args.signer}: ")
        ap = store.sign(args.approval_id, args.signer, pw, args.meaning, args.reason,
                        Policy.load(args.policy).approval.separate_signer)
        print(f"{ap.id} {ap.status} by {ap.signature.printed_name} at {ap.signature.signed_at}")
    elif args.cmd == "add-signer":
        pw1 = getpass.getpass("New signer password (min 12 chars): ")
        if pw1 != getpass.getpass("Repeat: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        ApprovalStore(root).add_signer(args.user_id, args.name, pw1)
        print(f"Signer {args.user_id} ({args.name}) registered.")
    elif args.cmd == "validation-summary":
        gw = build_gateway(args.target, policy=Policy(require_identity=False), registry=Registry(args.registry), root=root)

        async def tools():
            return [(t.name, t.meta, str(t.version) if t.version else None) for t in await gw.list_tools()]
        print(validation_summary(Registry(args.registry), asyncio.run(tools()), args.junit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
