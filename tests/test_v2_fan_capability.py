"""Preserve capability conversion while extracting its normalization helper."""

import pytest

from pizone import v2

from .v2_fixtures import SAMPLE_SYSTEM_V2


@pytest.mark.parametrize(
    ("enabled", "kind", "expected"),
    [
        (0, 1, "disabled"),
        (None, 1, "disabled"),
        ("0", 1, "disabled"),
        (1, 1, "3-speed"),
        (0, 2, "disabled"),
        (1, 3, "4-speed"),
        ("0", "4", "disabled"),
        (1, None, "unknown"),
        (1, 99, "unknown"),
    ],
)
def test_fan_capability_conversion(enabled, kind, expected) -> None:
    system = {**SAMPLE_SYSTEM_V2["SystemV2"], "FanAutoEn": enabled, "FanAutoType": kind}
    assert v2.system_v2_to_settings(system, "000025841")["FanAuto"] == expected
