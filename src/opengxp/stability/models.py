"""Shared input models for the stability tools."""

from __future__ import annotations

import csv
import io
from typing import Literal

from pydantic import BaseModel, Field

from opengxp.core import GxPInputError


class StabilityPoint(BaseModel):
    batch: str = Field(description="Batch / lot identifier.")
    time_months: float = Field(ge=0, description="Time on stability, months.")
    value: float = Field(description="Measured result in the attribute's unit.")


class Attribute(BaseModel):
    name: str = Field(description="e.g. 'Assay', 'Impurity B', 'Total degradation products'.")
    direction: Literal["decreasing", "increasing", "both"] = Field(
        description="Known direction of change. 'decreasing' -> lower one-sided 95% bound vs lower_spec; "
                    "'increasing' -> upper one-sided bound vs upper_spec; 'both' -> two-sided bounds vs both specs (Q1E 2.6)."
    )
    lower_spec: float | None = Field(None, description="Lower acceptance criterion (e.g. 95.0 % label claim).")
    upper_spec: float | None = Field(None, description="Upper acceptance criterion (e.g. NMT 1.0 % for a degradant).")
    unit: str = "%"
    transform: Literal["none", "log"] = Field(
        "none", description="'log' fits ln(value) (e.g. first-order loss); specs are transformed the same way.")


def parse_csv(csv_text: str) -> list[StabilityPoint]:
    """Accept CSV with header batch,time_months,value (aliases: lot, time, month(s), result)."""
    alias = {"lot": "batch", "time": "time_months", "month": "time_months", "months": "time_months",
             "result": "value", "batch": "batch", "time_months": "time_months", "value": "value"}
    rows = list(csv.DictReader(io.StringIO(csv_text.strip())))
    if not rows:
        raise GxPInputError("csv_text has no data rows. Expected header: batch,time_months,value")
    out = []
    for i, r in enumerate(rows, 2):
        m = {alias.get(k.strip().lower()): v for k, v in r.items() if k and alias.get(k.strip().lower())}
        try:
            out.append(StabilityPoint(batch=str(m["batch"]).strip(), time_months=float(m["time_months"]),
                                      value=float(m["value"])))
        except (KeyError, TypeError, ValueError) as e:
            raise GxPInputError(f"csv_text line {i}: could not read batch,time_months,value ({e}). "
                                "Use a header row 'batch,time_months,value' and numeric time/value.") from e
    return out
