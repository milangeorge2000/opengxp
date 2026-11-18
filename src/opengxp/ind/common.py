"""Shared sources and helpers for the ind server."""

from __future__ import annotations

from opengxp.core import Citation

M12_URL = "https://database.ich.org/sites/default/files/ICH_M12_Step4_Guideline_2024_0521_0.pdf"
FDA_MRSD_URL = "https://www.fda.gov/media/72309/download"
M3_URL = "https://database.ich.org/sites/default/files/M3_R2__Guideline.pdf"
M3_QA_URL = "https://www.ema.europa.eu/system/files/documents/other/wc500109298_en.pdf"
S9_URL = "https://database.ich.org/sites/default/files/S9_Guideline.pdf"
E14S7B_QA_URL = "https://database.ich.org/sites/default/files/E14-S7B_QAs_Step4_2022_0221.pdf"

FU_FLOOR = 0.01  # M12 2.1.2.1 and E14/S7B Q&A 1.2: fu,p set to 1% unless a measured value <1% is shown reliable


def effective_fu(fu_p: float, reliable_below_1pct: bool) -> tuple[float, str | None]:
    """Unbound fraction to use, and an assumption line when the 1% floor was applied."""
    if fu_p < FU_FLOOR and not reliable_below_1pct:
        return FU_FLOOR, (f"Measured fu,p {fu_p:g} is below 1% and its reliability was not demonstrated, "
                          "so fu,p = 0.01 was used.")
    return fu_p, None


def sig(x: float, n: int = 4) -> float:
    """Round to n significant figures for reporting (calculations keep full precision)."""
    return float(f"{x:.{n}g}")


def cite(guideline: str, section: str, rule: str | None, url: str) -> Citation:
    return Citation(guideline=guideline, section=section, rule=rule, url=url)
