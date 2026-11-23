"""Load an FDA Inactive Ingredient Database (IID) release into Supabase as a new, versioned dataset.

Steps: parse the FDA xlsx -> normalise -> stage a version (status 'staged', invisible to readers) ->
insert rows -> publish atomically (row count must match, the previous version is retired, never deleted).
Runs as the ref_ingest database role, never with the service_role key.

    opengxp-ref-ingest iid --file IIR_OCOMM.xlsx --label "October 2026" \
        --source-url https://www.fda.gov/media/195788/download --dsn "$REF_INGEST_DSN"
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import astuple, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

EXPECTED_HEADER = (
    "INGREDIENT_NAME", "ROUTE", "DOSAGE_FORM", "CAS_NUMBER", "UNII", "POTENCY_AMOUNT", "POTENCY_UNIT",
    "MAXIMUM_DAILY_EXPOSURE", "MAXIMUM_DAILY_EXPOSURE_UNIT", "RECORD_UPDATED",
)
MISSING = {"", "NA", "N/A", "NONE"}


class IngestError(ValueError):
    """The source file is not what we expect. Nothing is loaded."""


@dataclass(frozen=True)
class IIDRow:
    row_no: int
    ingredient_name: str
    route: str
    dosage_form: str
    cas_number: str | None
    unii: str | None
    potency_amount: Decimal | None
    potency_unit: str | None
    max_daily_exposure: Decimal | None
    max_daily_exposure_unit: str | None
    record_updated: bool


def _text(v) -> str | None:
    if v is None:
        return None
    s = " ".join(str(v).split()).upper()
    return None if s in MISSING else s


def _number(v, field: str, row_no: int) -> Decimal | None:
    if v is None or (isinstance(v, str) and v.strip().upper() in MISSING):
        return None
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation as e:
        raise IngestError(f"row {row_no}: {field} is not a number: {v!r}") from e
    if d < 0 or not d.is_finite():
        raise IngestError(f"row {row_no}: {field} must be a non-negative number, got {v!r}")
    return Decimal(int(d)) if d == d.to_integral_value() else d


def format_cas(v) -> str | None:
    """FDA stores CAS numbers as integers without dashes (98555). Rebuild '98-55-5' and verify the check digit."""
    s = _text(v)
    if s is None:
        return None
    digits = s.replace("-", "")
    if not digits.isdigit() or not 5 <= len(digits) <= 10:
        raise IngestError(f"CAS number {v!r} is not 5-10 digits")
    body, check = digits[:-1], int(digits[-1])
    if sum(int(d) * (i + 1) for i, d in enumerate(reversed(body))) % 10 != check:
        raise IngestError(f"CAS number {v!r} fails its check digit")
    return f"{body[:-2]}-{body[-2:]}-{check}"


def parse_iid_xlsx(path: str | Path) -> list[IIDRow]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = tuple(_text(h) for h in next(it))
        if header[: len(EXPECTED_HEADER)] != EXPECTED_HEADER:
            raise IngestError(f"unexpected columns {header}; expected {EXPECTED_HEADER}. FDA changed the file format: "
                              "update the parser and its tests before loading.")
        rows: list[IIDRow] = []
        for i, r in enumerate(it, start=1):
            r = tuple(r) + (None,) * (len(EXPECTED_HEADER) - len(r))
            if all(v is None or str(v).strip() == "" for v in r):
                continue
            name, route, form = _text(r[0]), _text(r[1]), _text(r[2])
            if not (name and route and form):
                raise IngestError(f"row {i}: ingredient, route and dosage form are required, got {r[:3]!r}")
            unii = _text(r[4])
            if unii is not None and not (len(unii) == 10 and unii.isalnum()):
                raise IngestError(f"row {i}: UNII {r[4]!r} is not a 10-character code")
            updated = _text(r[9])
            if updated not in (None, "Y", "N"):
                raise IngestError(f"row {i}: RECORD_UPDATED must be Y, N or blank, got {r[9]!r}")
            try:
                cas = format_cas(r[3])
            except IngestError as e:
                raise IngestError(f"row {i}: {e}") from None
            rows.append(IIDRow(
                row_no=i, ingredient_name=name, route=route, dosage_form=form, cas_number=cas, unii=unii,
                potency_amount=_number(r[5], "POTENCY_AMOUNT", i),
                potency_unit=None if r[6] is None or str(r[6]).strip().upper() in MISSING else str(r[6]).strip(),
                max_daily_exposure=_number(r[7], "MAXIMUM_DAILY_EXPOSURE", i),
                max_daily_exposure_unit=None if _text(r[8]) is None else str(r[8]).strip(),
                record_updated=updated == "Y",
            ))
    finally:
        wb.close()
    if not rows:
        raise IngestError("the file has no data rows")
    return rows


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_and_publish(conn, rows: list[IIDRow], *, label: str, source_url: str, source_sha256: str,
                     publish: bool = True) -> int:
    """Stage a new IID version, insert its rows and (optionally) publish it. Returns the version id.
    `conn` is a psycopg connection logged in as a member of ref_ingest. All-or-nothing."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("select id, status from ref.dataset_versions where dataset = 'iid' and source_sha256 = %s",
                        (source_sha256,))
            if (existing := cur.fetchone()) is not None:
                raise IngestError(f"this file is already loaded as version {existing[0]} ({existing[1]})")
            cur.execute(
                "insert into ref.dataset_versions (dataset, label, source_url, source_sha256, declared_rows) "
                "values ('iid', %s, %s, %s, %s) returning id",
                (label, source_url, source_sha256, len(rows)))
            version_id = cur.fetchone()[0]
            # COPY is refused on tables with row-level security, so insert in batches (pipelined by psycopg).
            cur.executemany(
                "insert into ref.iid_entries (version_id, row_no, ingredient_name, route, dosage_form, cas_number, "
                "unii, potency_amount, potency_unit, max_daily_exposure, max_daily_exposure_unit, record_updated) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(version_id, *astuple(r)) for r in rows])
            if publish:
                cur.execute("select ref.publish_version(%s)", (version_id,))
    return version_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="opengxp-ref-ingest", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="dataset", required=True)
    iid = sub.add_parser("iid", help="Load an FDA Inactive Ingredient Database xlsx")
    iid.add_argument("--file", required=True, help="IIR_OCOMM.xlsx from the FDA IID download zip")
    iid.add_argument("--label", required=True, help='Release label, e.g. "October 2026"')
    iid.add_argument("--source-url", required=True, help="FDA download URL the file came from")
    iid.add_argument("--dsn", default=os.environ.get("REF_INGEST_DSN"),
                     help="Postgres connection string of the ingest user (default: $REF_INGEST_DSN)")
    iid.add_argument("--dry-run", action="store_true", help="Parse and validate only; load nothing")
    iid.add_argument("--no-publish", action="store_true", help="Stage the version but leave it unpublished")
    a = p.parse_args(argv)

    try:
        rows = parse_iid_xlsx(a.file)
    except IngestError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    digest = sha256_file(a.file)
    print(f"parsed {len(rows)} rows, {len({r.ingredient_name for r in rows})} ingredients, sha256 {digest}")
    if a.dry_run:
        return 0
    if not a.dsn:
        print("refused: no --dsn or REF_INGEST_DSN given", file=sys.stderr)
        return 2
    import psycopg

    with psycopg.connect(a.dsn) as conn:
        try:
            vid = load_and_publish(conn, rows, label=a.label, source_url=a.source_url, source_sha256=digest,
                                   publish=not a.no_publish)
        except (IngestError, psycopg.Error) as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
    print(f"version {vid} {'staged' if a.no_publish else 'published'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
