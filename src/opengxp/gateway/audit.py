"""Append-only, hash-chained audit trail (JSON Lines).

Each record carries seq, UTC timestamp, prev_hash and hash = sha256(prev_hash + canonical JSON of the
record without its hash). Any edit, deletion or reordering of a record breaks the chain from that point,
which verify() reports. Writes take an exclusive file lock and are fsync'ed.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_hash(prev_hash: str, body: dict) -> str:
    return sha256(prev_hash + canonical(body))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class VerifyResult:
    ok: bool
    records: int
    last_hash: str
    first_bad_seq: int | None = None
    reason: str | None = None


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def _tail(self, f) -> tuple[int, str]:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return 0, GENESIS
        # read backwards to the last full line
        block, data = 4096, b""
        pos = size
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            lines = data.strip().split(b"\n")
            if len(lines) > 1 or pos == 0:
                last = json.loads(lines[-1])
                return int(last["seq"]), last["hash"]
        raise RuntimeError("unreachable")

    def append(self, event: dict) -> dict:
        with open(self.path, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                seq, prev = self._tail(f)
                body = {"seq": seq + 1, "timestamp": event.pop("timestamp", None) or utc_now(), **event, "prev_hash": prev}
                body["hash"] = _record_hash(prev, body)
                f.seek(0, os.SEEK_END)
                f.write((canonical(body) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
                return body
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def __iter__(self) -> Iterator[dict]:
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> VerifyResult:
        prev, n, expected_seq = GENESIS, 0, 1
        with open(self.path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    return VerifyResult(False, n, prev, expected_seq, f"line {lineno} is not valid JSON")
                h = rec.pop("hash", None)
                if rec.get("seq") != expected_seq:
                    return VerifyResult(False, n, prev, expected_seq,
                                        f"sequence gap: expected {expected_seq}, found {rec.get('seq')} (record deleted or reordered)")
                if rec.get("prev_hash") != prev:
                    return VerifyResult(False, n, prev, expected_seq, "prev_hash does not match the previous record")
                if _record_hash(prev, rec) != h:
                    return VerifyResult(False, n, prev, expected_seq, "record content does not match its hash (edited)")
                prev, n, expected_seq = h, n + 1, expected_seq + 1
        return VerifyResult(True, n, prev)
