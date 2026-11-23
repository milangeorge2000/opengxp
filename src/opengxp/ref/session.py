"""Local sign-in for stdio use: `opengxp-ref login` exchanges the user's own email and password (typed by
the user at the prompt) for a Supabase session, stores only the tokens in a 0600 file, and refreshes them.
The password is never stored or logged."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import time
from pathlib import Path

import httpx2

from opengxp.ref.client import RefAccessError, config

ENV_SESSION = "OPENGXP_REF_SESSION"


def session_path() -> Path:
    if p := os.environ.get(ENV_SESSION):
        return Path(p)
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "opengxp" / "ref-session.json"


def _save(data: dict) -> None:
    p = session_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, p)
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def _load() -> dict | None:
    p = session_path()
    if not p.exists():
        return None
    if p.stat().st_mode & 0o077:
        raise RefAccessError(f"{p} is readable by other users; delete it and run `opengxp-ref login` again.")
    return json.loads(p.read_text())


def _token_request(grant: str, body: dict) -> dict:
    url, key = config()
    r = httpx2.post(f"{url}/auth/v1/token?grant_type={grant}", json=body,
                    headers={"apikey": key, "Content-Type": "application/json"}, timeout=15)
    if r.status_code != 200:
        raise RefAccessError("Sign-in failed: check the email and password, or that your account is confirmed."
                             if grant == "password" else "Session refresh failed; run `opengxp-ref login` again.")
    d = r.json()
    return {"access_token": d["access_token"], "refresh_token": d["refresh_token"],
            "expires_at": int(d.get("expires_at") or time.time() + int(d.get("expires_in", 3600))),
            "email": (d.get("user") or {}).get("email")}


def login(email: str, password: str) -> dict:
    s = _token_request("password", {"email": email, "password": password})
    _save(s)
    return s


def current_access_token() -> str:
    s = _load()
    if s is None:
        raise RefAccessError("Not signed in to the reference data service. Run `opengxp-ref login --email <you>`.")
    if s["expires_at"] - time.time() < 60:
        s = {**_token_request("refresh_token", {"refresh_token": s["refresh_token"]}), "email": s.get("email")}
        _save(s)
    return s["access_token"]


def logout() -> bool:
    p = session_path()
    if p.exists():
        p.unlink()
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="opengxp-ref", description="Sign in to the OpenGxP reference data service.")
    sub = p.add_subparsers(dest="cmd", required=True)
    li = sub.add_parser("login", help="Sign in with your email and password (prompted, never stored)")
    li.add_argument("--email", required=True)
    sub.add_parser("logout", help="Delete the local session")
    sub.add_parser("whoami", help="Show your roles and the datasets they grant")
    a = p.parse_args(argv)
    try:
        if a.cmd == "login":
            s = login(a.email, getpass.getpass("Password: "))
            print(f"Signed in as {s.get('email') or a.email}. Session stored in {session_path()} (mode 600).")
        elif a.cmd == "logout":
            print("Signed out." if logout() else "No session to remove.")
        else:
            import anyio

            from opengxp.ref.client import RefClient

            rows = anyio.run(RefClient().rpc, "my_access")
            print("\n".join(f"{r['role']}: {r['dataset']}" for r in rows) or "No roles assigned. Ask an administrator.")
    except RefAccessError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
