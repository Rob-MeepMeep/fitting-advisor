"""Golden fit tests against user-supplied Pyfa GUI values.

Expected values in tests/golden/*.expected.json MUST come from the user reading
the Pyfa GUI. Stubs with status == 'awaiting_user_values' are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from advisor import eft, engine, validate
from advisor.models import SkillSet

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_EFTS = sorted(GOLDEN_DIR.glob("*.eft"))
REL_TOL = 0.005  # 0.5% relative tolerance for Pyfa GUI rounding


def _assert_close(actual: float, expected: float, label: str, abs_tol: float = 0.0) -> None:
    if expected == 0:
        assert abs(actual) <= max(1e-3, abs_tol), f"{label}: expected 0, got {actual}"
    else:
        assert actual == pytest.approx(expected, rel=REL_TOL, abs=abs_tol), (
            f"{label}: actual={actual} vs expected={expected} (tol={REL_TOL:.1%}, abs={abs_tol})"
        )


@pytest.mark.parametrize("eft_path", GOLDEN_EFTS, ids=lambda p: p.stem)
def test_golden_fit(eft_path: Path):
    expected_path = eft_path.with_suffix(".expected.json")
    assert expected_path.is_file(), f"Missing expected JSON file: {expected_path}"

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if expected.get("status") == "awaiting_user_values":
        pytest.skip("awaiting_user_values")

    fit = eft.parse_eft(eft_path.read_text(encoding="utf-8"))
    skills = SkillSet(preset=expected.get("skills", "all5"))

    if expected.get("expected_valid") is False:
        result = validate.validate_fit(fit, skills)
        assert not result.valid
        for required_code in expected.get("expected_issue_codes", []):
            assert any(i.code == required_code for i in result.issues), (
                f"Expected violation {required_code}, got {[i.code for i in result.issues]}"
            )
        return

    activate_drones = bool(expected.get("drones_active", True))
    stats = engine.calculate(fit, skills, activate_drones=activate_drones)
    exp = expected["stats"]
    for field in (
        "dps_total",
        "volley_total",
        "ehp_total",
        "ehp_shield",
        "ehp_armor",
        "ehp_hull",
        "cpu_used",
        "pg_used",
        "calibration_used",
        "max_speed",
        "align_time",
    ):
        if field in exp and exp[field] is not None:
            _assert_close(getattr(stats, field), float(exp[field]), field)

    if "cap_stable" in exp and exp["cap_stable"] is not None:
        assert stats.cap_stable is bool(exp["cap_stable"])
        if not stats.cap_stable and exp.get("cap_duration_s") is not None:
            # Pyfa GUI (capacitorViewFull.py:165-168) truncates fit.capState to integer seconds via %ds / %dm%ds
            _assert_close(stats.cap_duration_s or 0.0, float(exp["cap_duration_s"]), "cap_duration_s", abs_tol=1.0)
        elif stats.cap_stable and exp.get("cap_stable_pct") is not None:
            _assert_close(stats.cap_stable_pct or 0.0, float(exp["cap_stable_pct"]), "cap_stable_pct")
