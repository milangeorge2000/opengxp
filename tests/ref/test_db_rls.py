"""Access control and data integrity of the ref schema, on real Postgres with a minimal Supabase stand-in
(tests/ref/supabase_stub.sql). Requests are made exactly as PostgREST makes them: `set local role
authenticated` plus the JWT claims in `request.jwt.claims`.

Skipped unless OPENGXP_TEST_PG points at a Postgres superuser connection, e.g.
    docker run -d --name opengxp-pg -e POSTGRES_PASSWORD=pg -p 54329:5432 postgres:17
    OPENGXP_TEST_PG=postgresql://postgres:pg@localhost:54329/postgres uv run pytest tests/ref
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo

from opengxp.core.errors import GxPInputError
from opengxp.ref.client import RefAccessError
from opengxp.ref.iid_ingest import (
    IngestError,
    load_and_publish,
    parse_iid_xlsx,
    sha256_file,
)

pytestmark = pytest.mark.skipif(not os.environ.get("OPENGXP_TEST_PG"), reason="set OPENGXP_TEST_PG to run database tests")

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "iid_oct2026_sample.xlsx"
DB = "opengxp_ref_test"
U_FORM, U_SRC, U_NONE = (uuid.UUID(int=i) for i in (1, 2, 3))


@pytest.fixture(scope="module")
def db():
    base = os.environ["OPENGXP_TEST_PG"]
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"drop database if exists {DB} with (force)")
        admin.execute(f"create database {DB}")
    dsn = make_conninfo(base, dbname=DB)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((ROOT / "tests" / "ref" / "supabase_stub.sql").read_text())
        c.execute(next((ROOT / "supabase" / "migrations").glob("*_ref_schema.sql")).read_text())
        c.execute("do $$ begin if not exists (select from pg_roles where rolname='ingest_bot') then "
                  "create role ingest_bot login password 'ingest-test'; end if; end $$")
        c.execute("grant ref_ingest to ingest_bot")
        c.execute("insert into auth.users (id) values (%s), (%s), (%s)", (U_FORM, U_SRC, U_NONE))
        c.execute("insert into ref.user_roles (user_id, role) values (%s, 'formulation'), (%s, 'sourcing')", (U_FORM, U_SRC))
    ingest_dsn = make_conninfo(base, dbname=DB, user="ingest_bot", password="ingest-test")
    with psycopg.connect(ingest_dsn) as ic:
        vid = load_and_publish(ic, parse_iid_xlsx(FIXTURE), label="October 2026",
                               source_url="https://www.fda.gov/media/195788/download", source_sha256=sha256_file(FIXTURE))
    yield {"dsn": dsn, "ingest_dsn": ingest_dsn, "version": vid}


@contextmanager
def as_user(dsn: str, uid: uuid.UUID | None):
    """A transaction with the privileges PostgREST would give this request (anon when uid is None)."""
    with psycopg.connect(dsn) as c, c.transaction(force_rollback=True):
        if uid is None:
            c.execute("set local role anon")
        else:
            c.execute("set local role authenticated")
            c.execute("select set_config('request.jwt.claims', %s, true)",
                      (json.dumps({"sub": str(uid), "role": "authenticated"}),))
        yield c


def rpc(c, fn: str, **args):
    q = sql.SQL("select * from api.{}({})").format(
        sql.Identifier(fn), sql.SQL(", ").join(sql.SQL("{} => {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in args))
    cur = c.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(q, args).fetchall()


# ------------------------------------------------------------------ who can read what
def test_anonymous_has_no_access(db):
    for stmt in ("select * from api.dataset_status()", "select count(*) from ref.iid_entries"):
        with as_user(db["dsn"], None) as c, pytest.raises(errors.InsufficientPrivilege):
            c.execute(stmt)


def test_user_without_role_sees_nothing(db):
    with as_user(db["dsn"], U_NONE) as c:
        assert rpc(c, "dataset_status") == []
        assert rpc(c, "iid_rows", ingredient="MAGNESIUM STEARATE") == []
        assert rpc(c, "resolve_ingredient", q="magnesium stearate") == []
        assert c.execute("select count(*) from ref.iid_entries").fetchone()[0] == 0


def test_role_without_iid_dataset_sees_nothing(db):
    with as_user(db["dsn"], U_SRC) as c:
        assert rpc(c, "dataset_status") == [] and rpc(c, "iid_rows", ingredient="MAGNESIUM STEARATE") == []
        assert rpc(c, "my_access") == [{"role": "sourcing", "dataset": "dmf"}]


def test_formulation_user_reads_exact_rows(db):
    with as_user(db["dsn"], U_FORM) as c:
        st = rpc(c, "dataset_status")
        assert [(s["dataset"], s["label"], s["row_count"]) for s in st] == [("iid", "October 2026", 164)]
        rows = rpc(c, "iid_rows", ingredient=" magnesium stearate ", route="oral", dosage_form="tablet")
        assert [(r["row_no"], r["max_daily_exposure"], r["max_daily_exposure_unit"]) for r in rows] == [(42, 980, "mg")]


def test_resolve_ranks_exact_cas_unii_and_similar(db):
    with as_user(db["dsn"], U_FORM) as c:
        assert rpc(c, "resolve_ingredient", q="magnesium stearate")[0]["matched_on"] == "exact_name"
        cas = rpc(c, "resolve_ingredient", q="1406-18-4")[0]
        assert (cas["ingredient_name"], cas["matched_on"]) == (".ALPHA.-TOCOPHEROL", "cas")
        assert rpc(c, "resolve_ingredient", q="H4N855PNZ1")[0]["matched_on"] == "unii"
        poly = rpc(c, "resolve_ingredient", q="polysorbate")
        assert {p["ingredient_name"] for p in poly} >= {"POLYSORBATE 20", "POLYSORBATE 80"}
        assert all(p["matched_on"] == "prefix" for p in poly[:2])
        sim = rpc(c, "resolve_ingredient", q="magnesium stearat")
        assert sim[0]["ingredient_name"] == "MAGNESIUM STEARATE"
        assert rpc(c, "resolve_ingredient", q="%") == []      # no wildcard injection: too short and literal
        forms = rpc(c, "resolve_dosage_form", q="tablet film coated", route="ORAL")
        assert "TABLET, FILM COATED" in [f["dosage_form"] for f in forms[:3]]


def test_list_vocab_validates_kind(db):
    with as_user(db["dsn"], U_FORM) as c:
        assert {"ORAL", "INTRAVENOUS"} <= {r["value"] for r in rpc(c, "list_vocab", kind="route")}
    with as_user(db["dsn"], U_FORM) as c, pytest.raises(errors.InvalidParameterValue):
        rpc(c, "list_vocab", kind="ingredient_name")


def test_users_see_only_their_own_roles(db):
    with as_user(db["dsn"], U_FORM) as c:
        assert c.execute("select user_id, role from ref.user_roles").fetchall() == [(U_FORM, "formulation")]


# ------------------------------------------------------------------ nobody but ingest writes
@pytest.mark.parametrize("stmt", [
    "insert into ref.user_roles (user_id, role) values ('00000000-0000-0000-0000-000000000003', 'admin')",
    "insert into ref.dataset_versions (dataset, label, source_url, source_sha256, declared_rows) "
    "values ('iid', 'x', 'https://x', repeat('b', 64), 1)",
    "delete from ref.iid_entries",
    "select ref.publish_version(1)",
    "select ref.custom_access_token_hook('{}'::jsonb)",
])
def test_authenticated_users_cannot_write_or_escalate(db, stmt):
    with as_user(db["dsn"], U_FORM) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute(stmt)


def test_published_rows_are_immutable(db):
    with psycopg.connect(db["dsn"]) as c:
        for stmt in ("update ref.iid_entries set potency_amount = 9999 where row_no = 1",
                     "delete from ref.iid_entries where row_no = 1",
                     "update ref.dataset_versions set label = 'tampered'",
                     "delete from ref.dataset_versions"):
            with c.transaction(force_rollback=True), pytest.raises(errors.RaiseException):
                c.execute(stmt)


def test_same_file_cannot_be_loaded_twice(db):
    with psycopg.connect(db["ingest_dsn"]) as ic, pytest.raises(IngestError, match="already loaded"):
        load_and_publish(ic, parse_iid_xlsx(FIXTURE), label="dup", source_url="https://x",
                         source_sha256=sha256_file(FIXTURE))


def test_staged_version_is_invisible_and_bad_count_cannot_publish(db):
    rows = parse_iid_xlsx(FIXTURE)[:10]
    with psycopg.connect(db["ingest_dsn"]) as ic:
        staged = load_and_publish(ic, rows, label="staged test", source_url="https://x", source_sha256="c" * 64,
                                  publish=False)
    with as_user(db["dsn"], U_FORM) as c:
        assert [s["label"] for s in rpc(c, "dataset_status")] == ["October 2026"]
    with psycopg.connect(db["ingest_dsn"]) as ic:
        with ic.transaction():
            ic.execute("insert into ref.dataset_versions (dataset, label, source_url, source_sha256, declared_rows) "
                       "values ('iid', 'short', 'https://x', %s, 999)", ("d" * 64,))
            short = ic.execute("select id from ref.dataset_versions where label = 'short'").fetchone()[0]
        with pytest.raises(errors.RaiseException, match="does not match|declared"):
            ic.execute("select ref.publish_version(%s)", (short,))
        ic.rollback()
        with ic.transaction():     # ingest can discard what it staged, nothing else
            ic.execute("delete from ref.iid_entries where version_id = %s", (staged,))
            ic.execute("delete from ref.dataset_versions where id in (%s, %s)", (staged, short))
        with pytest.raises(errors.InsufficientPrivilege):
            ic.execute("update ref.dataset_versions set status = 'retired' where id = %s", (db["version"],))


def test_token_hook_puts_roles_in_claims(db):
    with psycopg.connect(db["dsn"]) as c:
        c.execute("set role supabase_auth_admin")
        out = c.execute("select ref.custom_access_token_hook(%s::jsonb)",
                        (json.dumps({"user_id": str(U_FORM), "claims": {"sub": str(U_FORM), "role": "authenticated"}}),)
                        ).fetchone()[0]
    assert out["claims"]["roles"] == ["formulation"] and out["claims"]["role"] == "authenticated"


# ------------------------------------------------------------------ the MCP tools, end to end through the database
class SQLRef:
    """Stands in for RefClient: same RPC names, executed with PostgREST's privileges for one user."""

    def __init__(self, dsn, uid):
        self.dsn, self.uid = dsn, uid

    async def rpc(self, fn, args=None):
        args = {k: v for k, v in (args or {}).items()}
        try:
            with as_user(self.dsn, self.uid) as c:
                return [{k: (float(v) if hasattr(v, "is_finite") else v.isoformat() if hasattr(v, "isoformat") else v)
                         for k, v in r.items()} for r in rpc(c, fn, **args)]
        except errors.InsufficientPrivilege:
            raise RefAccessError("Access denied by the data service.") from None
        except errors.InvalidParameterValue as e:
            raise GxPInputError(str(e)) from None


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_tools_end_to_end(db, monkeypatch):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    import opengxp.ref.server as srv
    from opengxp.server import mcp

    monkeypatch.setattr(srv, "_client_factory", lambda: SQLRef(db["dsn"], U_FORM))
    async with Client(mcp) as client:
        r = (await client.call_tool("ref_resolve_ingredient", {"query": "polysorbate 80"})).structured_content
        assert r["exact"] and r["candidates"][0]["ingredient_name"] == "POLYSORBATE 80"
        chk = (await client.call_tool("ref_check_excipients", {
            "route": "ORAL", "dosage_form": "TABLET, FILM COATED", "units_per_day": 2,
            "excipients": [{"ingredient": "MAGNESIUM STEARATE", "amount": 5, "unit": "mg"},
                           {"ingredient": "POLYSORBATE 80", "amount": 30, "unit": "mg"}]})).structured_content
        assert [e["verdict"] for e in chk["excipients"]] == ["within_precedent", "above_precedent"]
        assert chk["dataset"]["label"] == "October 2026" and chk["findings"][0]["code"] == "ABOVE_IID_PRECEDENT"
        prec = (await client.call_tool("ref_excipient_precedents", {"ingredient": "POLYSORBATE 80", "route": "ORAL"})).structured_content
        assert prec["rows"] and all(x["route"] == "ORAL" for x in prec["rows"])

    monkeypatch.setattr(srv, "_client_factory", lambda: SQLRef(db["dsn"], U_NONE))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="no role"):
            await client.call_tool("ref_check_excipients", {
                "route": "ORAL", "dosage_form": "TABLET",
                "excipients": [{"ingredient": "MAGNESIUM STEARATE", "amount": 5, "unit": "mg"}]})


def test_revocation_is_immediate_and_audited(db):
    with psycopg.connect(db["dsn"], autocommit=True) as c:
        c.execute("delete from ref.user_roles where user_id = %s", (U_FORM,))
    with as_user(db["dsn"], U_FORM) as c:
        assert rpc(c, "dataset_status") == []
    with psycopg.connect(db["dsn"], autocommit=True) as c:
        log = c.execute("select action, role from ref.role_audit where user_id = %s order by id", (U_FORM,)).fetchall()
        c.execute("insert into ref.user_roles (user_id, role) values (%s, 'formulation')", (U_FORM,))
    assert log == [("insert", "formulation"), ("delete", "formulation")]
