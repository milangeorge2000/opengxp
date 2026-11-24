"""IID parsing and excipient verdicts, on real rows from the FDA IID October 2026 release
(tests/fixtures/iid_oct2026_sample.xlsx: 164 rows copied unchanged from IIR_OCOMM.xlsx, public domain)."""

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from opengxp.ref.excipients import (
    DatasetInfo,
    ExcipientInput,
    build_result,
    normalise_iid_unit,
    verdict_for,
)
from opengxp.ref.iid_ingest import IngestError, format_cas, parse_iid_xlsx

FIXTURE = Path(__file__).parents[1] / "fixtures" / "iid_oct2026_sample.xlsx"
DS = DatasetInfo(label="October 2026", source_url="https://www.fda.gov/media/195788/download",
                 source_sha256="a" * 64, row_count=9076)


@pytest.fixture(scope="module")
def rows():
    return parse_iid_xlsx(FIXTURE)


def _rows_for(rows, name, route, form):
    return [asdict(r) for r in rows if (r.ingredient_name, r.route, r.dosage_form) == (name, route, form)]


def test_parse_real_rows(rows):
    assert len(rows) == 164
    first = rows[0]
    assert (first.ingredient_name, first.route, first.dosage_form) == (
        ".ALPHA.-TOCOPHEROL", "INTRAVENOUS", "INJECTION, POWDER, FOR SUSPENSION")
    assert first.cas_number == "1406-18-4" and first.unii == "H4N855PNZ1"
    assert (first.potency_amount, first.potency_unit) == (Decimal("0.64"), "mg")
    assert any(r.unii is None for r in rows)            # FDA writes 'NA'
    assert any(r.record_updated for r in rows)
    # each row carries a per-unit limit, a daily limit, or neither; never both (true of the whole October 2026 file)
    assert not any(r.potency_amount is not None and r.max_daily_exposure is not None for r in rows)


def test_cas_formatting_and_check_digit():
    assert format_cas(98555) == "98-55-5"
    assert format_cas(7732185) == "7732-18-5"            # water
    assert format_cas("NA") is None and format_cas(None) is None
    with pytest.raises(IngestError, match="check digit"):
        format_cas(7732186)


def test_unexpected_header_refused(tmp_path):
    wb = Workbook()
    wb.active.append(["INGREDIENT", "ROUTE", "FORM"])
    wb.active.append(["X", "ORAL", "TABLET"])
    p = tmp_path / "bad.xlsx"
    wb.save(p)
    with pytest.raises(IngestError, match="unexpected columns"):
        parse_iid_xlsx(p)


@pytest.mark.parametrize("value,unit,expected", [
    ("1.05", "mg/1ml", (Decimal("1.05"), "mg/mL")),
    ("100", "mg/5ml", (Decimal(20), "mg/mL")),
    ("2", "ml/5ml", (Decimal(40), "%v/v")),
    ("0.5", "mg/mg", (Decimal(50), "%w/w")),
    ("3", "%w/v", (Decimal(3), "%w/v")),
    ("1", "ADJ PH", None),
    ("1", None, None),
])
def test_unit_normalisation(value, unit, expected):
    assert normalise_iid_unit(Decimal(value), unit) == expected


def test_daily_limit_within(rows):
    # MAGNESIUM STEARATE, ORAL, TABLET: FDA maximum daily exposure 980 mg (row 42)
    v = verdict_for(ExcipientInput(ingredient="MAGNESIUM STEARATE", amount=5, unit="mg"), "ORAL", "TABLET",
                    _rows_for(rows, "MAGNESIUM STEARATE", "ORAL", "TABLET"), units_per_day=4)
    assert v.verdict == "within_precedent"
    lim = v.limits[0]
    assert (lim.row_no, lim.limit_kind, lim.yours, lim.percent_of_limit) == (42, "daily", 20.0, 2.0)


def test_daily_limit_needs_units_per_day(rows):
    v = verdict_for(ExcipientInput(ingredient="MAGNESIUM STEARATE", amount=5, unit="mg"), "ORAL", "TABLET",
                    _rows_for(rows, "MAGNESIUM STEARATE", "ORAL", "TABLET"), units_per_day=None)
    assert v.verdict == "not_comparable" and "units_per_day" in v.limits[0].note


def test_per_unit_limit_above(rows):
    # POLYSORBATE 80, ORAL, TABLET, FILM COATED: FDA maximum 24 mg per unit (row 120)
    v = verdict_for(ExcipientInput(ingredient="POLYSORBATE 80", amount=30, unit="mg"), "ORAL", "TABLET, FILM COATED",
                    _rows_for(rows, "POLYSORBATE 80", "ORAL", "TABLET, FILM COATED"), units_per_day=2)
    assert v.verdict == "above_precedent"
    assert v.limits[0].percent_of_limit == 125.0 and v.limits[0].fda_value == "24 mg"


def test_concentration_converted_and_unit_mismatch(rows):
    rws = _rows_for(rows, ".ALPHA.-TOCOPHEROL", "ORAL", "SOLUTION")           # 1.05 mg/1ml
    ok = verdict_for(ExcipientInput(ingredient=".ALPHA.-TOCOPHEROL", amount=1, unit="mg/mL"), "ORAL", "SOLUTION", rws, None)
    assert ok.verdict == "within_precedent" and ok.limits[0].normalised_value == 1.05
    bad = verdict_for(ExcipientInput(ingredient=".ALPHA.-TOCOPHEROL", amount=0.1, unit="%w/v"), "ORAL", "SOLUTION", rws, None)
    assert bad.verdict == "not_comparable"


def test_no_precedent_and_result_envelope(rows):
    items = [ExcipientInput(ingredient="MAGNESIUM STEARATE", amount=5, unit="mg"),
             ExcipientInput(ingredient="POLYSORBATE 80", amount=30, unit="mg")]
    res = build_result(items, "ORAL", "TABLET, FILM COATED",
                       [_rows_for(rows, "MAGNESIUM STEARATE", "ORAL", "TABLET, FILM COATED"), []], 2, DS)
    assert [v.verdict for v in res.excipients] == ["within_precedent", "no_precedent"]
    assert {f.code for f in res.findings} == {"NO_IID_PRECEDENT"}
    assert res.citations[0].url == DS.source_url and "October 2026" in res.provenance.guideline_version
    assert len(res.provenance.inputs_hash) == 64
