"""Phase 2 tests: fit legality validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from advisor import eft, engine, validate
from advisor.models import DroneSpec, FitSpec, SkillSet

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


def test_explicit_drone_bandwidth_and_active_counts_validated():
    # Valid explicit active drones (5x Hammerhead II = 50 Mbit/s <= 75, 5 <= 5 max active)
    valid_fit = FitSpec(
        ship="Vexor",
        name="Valid Active Drones",
        drones=[DroneSpec(name="Hammerhead II", quantity=5, active=5)],
    )
    assert validate.validate_fit(valid_fit, SkillSet(preset="all5")).valid

    # Exceeds drone bandwidth (5x Ogre II = 125 m3 fits in 125 m3 bay, but 125 Mbit/s > 75 Mbit/s)
    over_bw = FitSpec(
        ship="Vexor",
        name="Over Bandwidth",
        drones=[DroneSpec(name="Ogre II", quantity=5, active=5)],
    )
    bw_res = validate.validate_fit(over_bw, SkillSet(preset="all5"))
    assert not bw_res.valid
    assert "drone_bandwidth_exceeded" in {i.code for i in bw_res.issues}

    # Exceeds maxActiveDrones (6x Hobgoblin II = 30 m3 & 30 Mbit/s, but 6 > 5 max active at All V)
    over_active_all5 = FitSpec(
        ship="Vexor",
        name="Over Active All5",
        drones=[DroneSpec(name="Hobgoblin II", quantity=6, active=6)],
    )
    act5_res = validate.validate_fit(over_active_all5, SkillSet(preset="all5"))
    assert not act5_res.valid
    assert "active_drones_exceeded" in {i.code for i in act5_res.issues}

    # Exceeds maxActiveDrones under All 0 (Drones skill 0 -> maxActiveDrones == 0)
    over_active_all0 = FitSpec(
        ship="Vexor",
        name="Over Active All0",
        drones=[DroneSpec(name="Hobgoblin II", quantity=1, active=1)],
    )
    act0_res = validate.validate_fit(over_active_all0, SkillSet(preset="all0"))
    assert not act0_res.valid
    assert "active_drones_exceeded" in {i.code for i in act0_res.issues}

    # Active exceeds carried quantity
    over_qty = FitSpec(
        ship="Vexor",
        name="Active Exceeds Qty",
        drones=[DroneSpec(name="Hobgoblin II", quantity=3, active=4)],
    )
    qty_res = validate.validate_fit(over_qty, SkillSet(preset="all5"))
    assert not qty_res.valid
    assert "active_drones_exceed_quantity" in {i.code for i in qty_res.issues}

    # Negative active count
    neg_active = FitSpec(
        ship="Vexor",
        name="Negative Active",
        drones=[DroneSpec(name="Hobgoblin II", quantity=3, active=-1)],
    )
    neg_res = validate.validate_fit(neg_active, SkillSet(preset="all5"))
    assert not neg_res.valid
    assert "invalid_active_drones" in {i.code for i in neg_res.issues}
