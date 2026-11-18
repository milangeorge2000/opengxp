"""Every branch of ICH Q1E 2.4/2.5 (Appendix A decision tree)."""

import pytest

from opengxp.core import GxPInputError
from opengxp.stability.extrapolation import extrapolation_limit as ex


@pytest.mark.parametrize("kw,expected,section", [
    # room temperature, no significant change at accelerated
    (dict(little_change_and_variability=True), 24, "2.4.1.1"),                                   # min(2*12, 12+12)
    (dict(), 24, "2.4.1.2"),                                                                     # stats performed
    (dict(statistical_analysis_performed=False), 18, "2.4.1.2"),                                 # min(1.5*12, 12+6)
    (dict(amenable_to_statistics=False), 18, "2.4.1.2"),
    (dict(statistical_analysis_performed=False, supporting_data=False), 12, "2.4.1.2"),
    # room temperature, significant change at accelerated
    (dict(accelerated_significant_change="within_3_months", intermediate_significant_change=True), 12, "2.4.2.2"),
    (dict(accelerated_significant_change="between_3_and_6_months", intermediate_significant_change=False), 18, "2.4.2.1"),
    (dict(accelerated_significant_change="within_3_months", intermediate_significant_change=False,
          statistical_analysis_performed=False), 15, "2.4.2.1"),                                 # X + 3
])
def test_room_temperature(kw, expected, section):
    r = ex("room_temperature", 12, **kw)
    assert r.max_shelf_life_months == expected
    assert r.section.endswith(section)


def test_caps_bind_differently_for_long_x():
    assert ex("room_temperature", 36).max_shelf_life_months == 48            # X + 12 < 2X
    assert ex("room_temperature", 6).max_shelf_life_months == 12             # 2X < X + 12
    assert ex("refrigerated", 24).max_shelf_life_months == 30                # X + 6 < 1.5X


@pytest.mark.parametrize("kw,expected,section", [
    (dict(little_change_and_variability=True), 18, "2.5.1.1"),
    (dict(), 18, "2.5.1.1"),
    (dict(statistical_analysis_performed=False), 15, "2.5.1.1"),
    (dict(accelerated_significant_change="between_3_and_6_months"), 12, "2.5.1.2"),
    (dict(accelerated_significant_change="within_3_months"), 12, "2.5.1.2"),
])
def test_refrigerated(kw, expected, section):
    r = ex("refrigerated", 12, **kw)
    assert (r.max_shelf_life_months, r.section.endswith(section)) == (expected, True)


def test_refrigerated_within_3_months_needs_excursion_discussion():
    assert any("excursions" in c for c in ex("refrigerated", 12, accelerated_significant_change="within_3_months").conditions)


def test_frozen_no_extrapolation():
    assert ex("freezer", 12).extrapolation_allowed is False
    assert ex("below_minus_20C", 12).max_shelf_life_months == 12


def test_needs_intermediate_result():
    with pytest.raises(GxPInputError, match="intermediate"):
        ex("room_temperature", 12, accelerated_significant_change="within_3_months")
