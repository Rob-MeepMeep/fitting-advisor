"""Phase 3 tests: variants (published-only), profiles, pricing (None != 0), and bounded search."""

from __future__ import annotations

from pathlib import Path

from advisor import eft, engine, pricing, profiles, validate, variants
from advisor.models import SkillSet

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_all_variants_are_published():
    """AGENTS.md §9: Never return unpublished items as candidates."""
    for mod_name in (
        "Damage Control II",
        "Gyrostabilizer II",
        "150mm Light AutoCannon II",
        "Small Trimark Armor Pump I",
        "5MN Y-T8 Compact Microwarpdrive",
    ):
        var_items = engine.get_module_variants(mod_name)
        assert len(var_items) > 0, f"Expected variants for {mod_name}"
        for item in var_items:
            assert item.published is True, f"Unpublished variant leaked: {item.name}"

        charges = engine.get_charge_variants(mod_name)
        for ch in charges:
            assert ch.published is True, f"Unpublished charge leaked: {ch.name}"


def test_missing_price_is_none_never_zero():
    """AGENTS.md §10: Missing or zero market data must be None, never 0."""
    provider = pricing.StaticPriceProvider({100: 5000.0, 200: 0.0, 300: None})
    prices = provider.get_prices([100, 200, 300, 999])
    assert prices[100] == 5000.0
    assert prices[200] is None
    assert prices[300] is None
    assert prices[999] is None


def test_budget_capped_profile_excludes_unknown_price_items():
    """AGENTS.md §10: Budget-capped profiles must never recommend items with unknown (None) price."""
    gyro_variants = engine.get_module_variants("Gyrostabilizer II")
    assert len(gyro_variants) >= 2

    # Give Tech II / Meta items known cheap prices, leave Officer / Faction / Deadspace as None
    price_map: dict[int, float | None] = {}
    for item in gyro_variants:
        mg = profiles.classify_meta_group(item)
        if mg in ("Tech I", "Meta", "Tech II"):
            price_map[item.type_id] = 1_000_000.0
        else:
            price_map[item.type_id] = None

    provider = pricing.StaticPriceProvider(price_map)
    pve = profiles.get_profile("pve")  # has budget_cap_isk
    assert pve.budget_cap_isk is not None

    legal = variants.list_legal_variants("Gyrostabilizer II", pve, provider)
    assert len(legal) > 0
    for opt in legal:
        assert opt.price_isk is not None, f"Unknown-price item {opt.name} leaked into budget-capped profile!"


def test_bling_profile_allows_unknown_price_items():
    """Profiles without a budget cap (bling) may include items with unknown prices."""
    provider = pricing.StaticPriceProvider({})  # all unknown
    bling = profiles.get_profile("bling")
    assert bling.budget_cap_isk is None

    legal = variants.list_legal_variants("Gyrostabilizer II", bling, provider)
    assert len(legal) > 0


def test_generate_candidates_all_valid_and_bounded():
    fit_spec = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    bling = profiles.get_profile("bling")
    provider = pricing.StaticPriceProvider({})

    baseline, cands = variants.generate_candidates(
        fit_spec=fit_spec,
        profile=bling,
        price_provider=provider,
        skills=SkillSet(preset="all5"),
        max_evaluations=20,
        top_n=5,
    )

    assert baseline.stats.ship == "Rifter"
    assert len(cands) > 0
    for cand in cands:
        val = validate.validate_fit(cand.fit_spec, SkillSet(preset="all5"))
        assert val.valid, f"Illegal candidate returned: {[i.message for i in val.issues]}"
        assert len(cand.changes) >= 1
