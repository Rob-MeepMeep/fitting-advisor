"""Phase 2 tests: EFT parsing, export, round-trip, and error handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from advisor import eft

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_EFTS = sorted(GOLDEN_DIR.glob("*.eft"))


@pytest.mark.parametrize("eft_path", GOLDEN_EFTS, ids=lambda p: p.stem)
def test_golden_round_trip(eft_path: Path):
    """For every golden fit, export(parse(text)) must produce an equivalent FitSpec."""
    original_text = eft_path.read_text(encoding="utf-8")
    parsed_1 = eft.parse_eft(original_text)
    exported = eft.export_eft(parsed_1)
    parsed_2 = eft.parse_eft(exported)

    assert parsed_2 == parsed_1


def test_unknown_item_raises_with_line_number_and_no_substitution():
    bad_eft = """[Rifter, Bad Item Test]
Damage Control II
Not A Real EVE Module 9000

5MN Y-T8 Compact Microwarpdrive
"""
    with pytest.raises(eft.EFTParseError) as exc_info:
        eft.parse_eft(bad_eft)
    assert exc_info.value.line_number == 3
    assert "Not A Real EVE Module 9000" in str(exc_info.value)


def test_unknown_ship_raises():
    bad_eft = "[FakeShipXYZ, Test]\nDamage Control II\n"
    with pytest.raises(eft.EFTParseError) as exc_info:
        eft.parse_eft(bad_eft)
    assert exc_info.value.line_number == 1


def test_slot_determined_from_gamedata_not_just_section_order():
    """Modules placed in missing/reordered sections must still land in their true slot."""
    scrambled = """[Rifter, Scrambled Sections]


200mm AutoCannon II, Republic Fleet EMP S

5MN Y-T8 Compact Microwarpdrive


Damage Control II
"""
    fit = eft.parse_eft(scrambled)
    assert [m.name for m in fit.highs] == ["200mm AutoCannon II"]
    assert fit.highs[0].charge == "Republic Fleet EMP S"
    assert [m.name for m in fit.mids] == ["5MN Y-T8 Compact Microwarpdrive"]
    assert [m.name for m in fit.lows] == ["Damage Control II"]


def test_offline_module_preserved():
    fit = eft.parse_eft((GOLDEN_DIR / "thrasher_offline.eft").read_text(encoding="utf-8"))
    offline_mods = [m for m in fit.highs if m.state == "offline"]
    assert len(offline_mods) == 1
    assert offline_mods[0].name == "Small Energy Nosferatu II"
    exported = eft.export_eft(fit)
    assert "Small Energy Nosferatu II /OFFLINE" in exported


def test_empty_slots_preserved():
    fit = eft.parse_eft((GOLDEN_DIR / "tristan_empty_slots.eft").read_text(encoding="utf-8"))
    assert any(m.empty for m in fit.lows)
    assert any(m.empty for m in fit.mids)
    assert all(m.empty for m in fit.highs)
    assert any(m.empty for m in fit.rigs)


def test_t3c_subsystems_parsed():
    fit = eft.parse_eft((GOLDEN_DIR / "tengu_t3c.eft").read_text(encoding="utf-8"))
    assert fit.ship == "Tengu"
    assert len(fit.subsystems) == 4
