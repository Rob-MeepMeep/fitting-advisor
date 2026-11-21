"""Phase 2 tests: fit legality validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from advisor import eft, engine, validate
from advisor.models import SkillSet

GOLDEN_DIR = Path(__file__).parent / "golden"
LEGAL_GOLDEN_EFTS = [p for p in sorted(GOLDEN_DIR.glob("*.eft")) if p.stem != "rifter_illegal"]


@pytest.mark.parametrize("eft_path", LEGAL_GOLDEN_EFTS, ids=lambda p: p.stem)
def test_legal_golden_fits_pass_validation(eft_path: Path):
    fit = eft.parse_eft(eft_path.read_text(encoding="utf-8"))
    result = validate.validate_fit(fit, SkillSet(preset="all5"))
    assert result.valid, f"{eft_path.name} failed validation: {[i.message for i in result.issues]}"


def test_illegal_golden_fit_reports_all_violations():
    fit = eft.parse_eft((GOLDEN_DIR / "rifter_illegal.eft").read_text(encoding="utf-8"))
    result = validate.validate_fit(fit, SkillSet(preset="all5"))
    assert not result.valid
    codes = {issue.code for issue in result.issues}
    assert "pg_exceeded" in codes
    assert "rig_size_mismatch" in codes
    assert "max_group_fitted_exceeded" in codes
    assert "invalid_charge" in codes
    assert len(result.issues) >= 4


def test_too_many_slots_and_hardpoints():
    over_slots = """[Rifter, Over Slots]
Damage Control II
Gyrostabilizer II
Gyrostabilizer II
Gyrostabilizer II
Gyrostabilizer II

200mm AutoCannon II
200mm AutoCannon II
200mm AutoCannon II
200mm AutoCannon II
"""
    fit = eft.parse_eft(over_slots)
    result = validate.validate_fit(fit, SkillSet(preset="all5"))
    assert not result.valid
    codes = {i.code for i in result.issues}
    assert "slots_exceeded" in codes
    assert "turret_hardpoints_exceeded" in codes


def test_ship_restriction_rejected():
    bad_ship_mod = """[Rifter, Ship Restriction]
Bastion Module I
"""
    fit = eft.parse_eft(bad_ship_mod)
    result = validate.validate_fit(fit, SkillSet(preset="all5"))
    assert not result.valid
    codes = {i.code for i in result.issues}
    assert "ship_restriction" in codes


def test_t3c_slot_counts_depend_on_subsystems():
    fit = eft.parse_eft((GOLDEN_DIR / "tengu_t3c.eft").read_text(encoding="utf-8"))
    stats = engine.calculate(fit, SkillSet(preset="all5"))
    assert stats.slots_total["subsystem"] == 4
    assert stats.slots_total["high"] >= 6
    assert stats.slots_total["med"] >= 6
    assert stats.slots_total["low"] >= 3
