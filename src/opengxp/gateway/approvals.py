"""Approval requests and electronic signatures.

A signature records the signer's printed name and user ID, the date/time and the meaning
(approved / rejected), plus the reason (21 CFR 11.50). The signer proves identity with two components,
user ID and password (11.200(a)); passwords are stored only as salted PBKDF2-SHA256 hashes.
An approval is bound to the requester, the tool and the SHA-256 of the exact arguments, expires after a
TTL, and is consumed by the first matching call (single use).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from opengxp.core import GxPInputError
from opengxp.gateway.audit import utc_now

PBKDF2_ITER = 310_000


class Signer(BaseModel):
    user_id: str
    printed_name: str
    salt: str
    pw_hash: str
    active: bool = True


class Signature(BaseModel):
    signer_id: str
    printed_name: str
    meaning: Literal["approved", "rejected"]
    reason: str
    signed_at: str


class Approval(BaseModel):
    id: str
    requested_by: str
    tool: str
    args_sha256: str
    args_preview: str
    requested_at: str
    expires_at: str
    status: Literal["pending", "approved", "rejected", "consumed", "expired"] = "pending"
    signature: Signature | None = None
    consumed_at: str | None = None


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITER).hex()


class ApprovalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "approvals").mkdir(parents=True, exist_ok=True)
        self.signers_path = self.root / "signers.json"

    @contextmanager
    def _lock(self):
        with open(self.root / ".lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # ---- signers -------------------------------------------------------------------------------------
    def _signers(self) -> dict[str, Signer]:
        if not self.signers_path.exists():
            return {}
        return {k: Signer(**v) for k, v in json.loads(self.signers_path.read_text()).items()}

    def add_signer(self, user_id: str, printed_name: str, password: str) -> None:
        if len(password) < 12:
            raise GxPInputError("Signer password must be at least 12 characters.")
        with self._lock():
            s = self._signers()
            salt = secrets.token_hex(16)
            s[user_id] = Signer(user_id=user_id, printed_name=printed_name, salt=salt, pw_hash=_hash_pw(password, salt))
            self.signers_path.write_text(json.dumps({k: v.model_dump() for k, v in s.items()}, indent=1))
            os.chmod(self.signers_path, 0o600)

    def _authenticate(self, user_id: str, password: str) -> Signer:
        s = self._signers().get(user_id)
        if not s or not s.active or not hmac.compare_digest(_hash_pw(password, s.salt), s.pw_hash):
            raise GxPInputError("Signature failed: unknown signer or wrong password.")
        return s

    # ---- approvals -----------------------------------------------------------------------------------
    def _path(self, aid: str) -> Path:
        if not aid.startswith("APR-") or not aid[4:].isalnum():
            raise GxPInputError(f"Invalid approval id '{aid}'.")
        return self.root / "approvals" / f"{aid}.json"

    def get(self, aid: str) -> Approval:
        p = self._path(aid)
        if not p.exists():
            raise GxPInputError(f"Unknown approval {aid}.")
        a = Approval(**json.loads(p.read_text()))
        if a.status in ("pending", "approved") and datetime.fromisoformat(a.expires_at) < datetime.now(timezone.utc):
            a.status = "expired"
            self._save(a)
        return a

    def _save(self, a: Approval) -> None:
        self._path(a.id).write_text(a.model_dump_json(indent=1))

    def list(self, status: str | None = None) -> list[Approval]:
        out = [self.get(p.stem) for p in sorted((self.root / "approvals").glob("APR-*.json"))]
        return [a for a in out if status is None or a.status == status]

    def request(self, requested_by: str, tool: str, args_sha256: str, args_preview: str, ttl_minutes: int) -> Approval:
        with self._lock():
            for a in self.list("pending"):  # reuse an open identical request
                if (a.requested_by, a.tool, a.args_sha256) == (requested_by, tool, args_sha256):
                    return a
            now = datetime.now(timezone.utc)
            a = Approval(id="APR-" + secrets.token_hex(4).upper(), requested_by=requested_by, tool=tool,
                         args_sha256=args_sha256, args_preview=args_preview[:500], requested_at=utc_now(),
                         expires_at=(now + timedelta(minutes=ttl_minutes)).isoformat(timespec="milliseconds"))
            self._save(a)
            return a

    def sign(self, aid: str, signer_id: str, password: str, meaning: Literal["approved", "rejected"], reason: str,
             separate_signer: bool = True) -> Approval:
        if not reason.strip():
            raise GxPInputError("A reason is required for an electronic signature.")
        signer = self._authenticate(signer_id, password)
        with self._lock():
            a = self.get(aid)
            if a.status != "pending":
                raise GxPInputError(f"{aid} is {a.status}; only pending approvals can be signed.")
            if separate_signer and signer.user_id == a.requested_by:
                raise GxPInputError("The requester cannot approve their own request (separate signer required by policy).")
            a.signature = Signature(signer_id=signer.user_id, printed_name=signer.printed_name, meaning=meaning,
                                    reason=reason.strip(), signed_at=utc_now())
            a.status = meaning
            self._save(a)
            return a

    def consume(self, requested_by: str, tool: str, args_sha256: str) -> Approval | None:
        """Find and consume a signed approval matching this exact call. Single use."""
        with self._lock():
            for a in self.list("approved"):
                if (a.requested_by, a.tool, a.args_sha256) == (requested_by, tool, args_sha256):
                    a.status, a.consumed_at = "consumed", utc_now()
                    self._save(a)
                    return a
        return None
