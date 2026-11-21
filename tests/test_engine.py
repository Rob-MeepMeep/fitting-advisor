"""Phase 1 tests: headless eos engine bootstrap, stats calculation, and cap simulation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from advisor import eft, engine
from advisor.models import SkillSet

RIFTER_MWD_EFT = """[Rifter, Cap Test]
Damage Control II
Gyrostabilizer II
Small Armor Repairer II
Multispectrum Energized Membrane II

5MN Y-T8 Compact Microwarpdrive
Warp Scrambler II
Fleeting Compact Stasis Webifier

200mm AutoCannon II, Republic Fleet EMP S
200mm AutoCannon II, Republic Fleet EMP S
200mm AutoCannon II, Republic Fleet EMP S

Small Projectile Burst Aerator I
Small Auxiliary Nano Pump I
Small Trimark Armor Pump I
"""


def test_engine_versions():
    versions = engine.get_versions()
    assert versions["pyfa_version"] != "unknown"
    assert versions["gamedata_version"] != "unknown"


def test_rifter_mwd_cap_simulation_is_unstable():
    """Verify cap simulation actually runs (MWD + active repper on Rifter must NOT be cap stable)."""
    fit_spec = eft.parse_eft(RIFTER_MWD_EFT)
    stats = engine.calculate(fit_spec, SkillSet(preset="all5"))

    assert stats.ship == "Rifter"
    assert stats.dps_total > 100.0
    assert stats.ehp_total > 2000.0
    assert stats.max_speed > 2000.0  # Active 5MN MWD
    assert stats.cap_stable is False
    assert stats.cap_duration_s is not None
    assert 5.0 < stats.cap_duration_s < 600.0
    assert stats.cap_used > stats.cap_recharge


def test_all0_vs_all5_skills_differ():
    fit_spec = eft.parse_eft(RIFTER_MWD_EFT)
    stats_all5 = engine.calculate(fit_spec, SkillSet(preset="all5"))
    stats_all0 = engine.calculate(fit_spec, SkillSet(preset="all0"))

    assert stats_all5.dps_total > stats_all0.dps_total
    assert stats_all5.cpu_output > stats_all0.cpu_output
    assert stats_all5.pg_output > stats_all0.pg_output


def test_engine_calculate_from_worker_thread():
    """Verify engine.calculate works across threads (e.g. FastAPI threadpool workers)."""
    fit_spec = eft.parse_eft(RIFTER_MWD_EFT)
    main_stats = engine.calculate(fit_spec, SkillSet(preset="all5"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker_stats = pool.submit(engine.calculate, fit_spec, SkillSet(preset="all5")).result()

    assert worker_stats.dps_total == main_stats.dps_total
    assert worker_stats.ehp_total == main_stats.ehp_total
    assert worker_stats.cap_duration_s == main_stats.cap_duration_s
