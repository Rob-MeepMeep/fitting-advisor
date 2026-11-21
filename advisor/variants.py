"""Candidate fit generation via bounded greedy & pairwise module/charge/rig swaps."""

from __future__ import annotations

from pydantic import BaseModel, Field

from advisor import eft, engine, pricing, profiles, validate
from advisor.models import FitSpec, FitStats, ItemInfo, SkillSet
from advisor.pricing import PriceProvider
from advisor.profiles import ProfileConfig


class CandidateFit(BaseModel):
    """A validated, engine-calculated alternative fit."""

    fit_spec: FitSpec
    eft_text: str
    stats: FitStats
    score: float
    total_cost_isk: float | None = None
    unknown_price_items: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)


class VariantOption(BaseModel):
    """A single legal module/charge/rig variant with price and meta group."""

    name: str
    type_id: int
    meta_group: str
    price_isk: float | None = None


def list_legal_variants(
    module_name: str,
    profile: ProfileConfig,
    price_provider: PriceProvider,
) -> list[VariantOption]:
    """Return published variants for ``module_name`` allowed by ``profile``."""
    raw_variants = engine.get_module_variants(module_name)
    if not raw_variants:
        return []

    prices = price_provider.get_prices([v.type_id for v in raw_variants])
    options: list[VariantOption] = []
    for item in raw_variants:
        price = prices.get(item.type_id)
        if not profiles.is_item_allowed_for_profile(item, profile, price):
            continue
        options.append(
            VariantOption(
                name=item.name,
                type_id=item.type_id,
                meta_group=profiles.classify_meta_group(item),
                price_isk=price,
            )
        )
    return options


def _slot_bucket(fit: FitSpec, slot: str):
    return {
        "low": fit.lows,
        "med": fit.mids,
        "high": fit.highs,
        "rig": fit.rigs,
        "subsystem": fit.subsystems,
    }[slot]


def _apply_group_swap(fit: FitSpec, slot: str, old_name: str, new_name: str) -> FitSpec:
    """Replace all modules named ``old_name`` in ``slot`` with ``new_name``."""
    clone = fit.model_copy(deep=True)
    for mod in _slot_bucket(clone, slot):
        if not mod.empty and mod.name == old_name:
            mod.name = new_name
    return clone


def _apply_charge_swap(fit: FitSpec, slot: str, mod_name: str, new_charge: str) -> FitSpec:
    clone = fit.model_copy(deep=True)
    for mod in _slot_bucket(clone, slot):
        if not mod.empty and mod.name == mod_name:
            mod.charge = new_charge
    return clone


def _select_representative_variants(
    variants: list[VariantOption],
    current_name: str,
    max_per_slot: int = 5,
) -> list[VariantOption]:
    """Keep a diverse subset (up to one per meta group / cheapest & highest) to bound search."""
    others = [v for v in variants if v.name != current_name]
    if len(others) <= max_per_slot:
        return others
    by_mg: dict[str, list[VariantOption]] = {}
    for v in others:
        by_mg.setdefault(v.meta_group, []).append(v)

    picked: list[VariantOption] = []
    # Pick best/representative from each meta group
    for mg in ("Tech II", "Meta", "Faction", "Deadspace", "Officer", "Storyline", "Tech I"):
        group = by_mg.get(mg)
        if not group:
            continue
        # Sort by price ascending (known prices first) and take first
        group.sort(key=lambda x: (x.price_isk is None, x.price_isk or 0.0, x.name))
        picked.append(group[0])
        if len(picked) >= max_per_slot:
            break
    return picked[:max_per_slot]


def generate_candidates(
    fit_spec: FitSpec,
    profile: ProfileConfig,
    price_provider: PriceProvider,
    skills: SkillSet | None = None,
    max_evaluations: int = 60,
    top_n: int = 5,
) -> tuple[CandidateFit, list[CandidateFit]]:
    """Generate and rank legal candidate fits for ``profile``.

    Uses bounded greedy single-module/charge/rig swaps followed by limited pairwise
    combinations of the best single swaps. Every candidate is validated and calculated
    by the engine; illegal or over-budget candidates are discarded.

    Returns ``(baseline_candidate, top_candidates)``.
    """
    baseline_stats = engine.calculate(fit_spec, skills)
    baseline_cost, _, baseline_unknown = pricing.estimate_fit_cost(fit_spec, price_provider)
    baseline_score = profiles.score_fit(
        baseline_stats, baseline_stats, baseline_cost, baseline_cost, profile
    )
    baseline = CandidateFit(
        fit_spec=fit_spec,
        eft_text=eft.export_eft(fit_spec),
        stats=baseline_stats,
        score=baseline_score,
        total_cost_isk=baseline_cost,
        unknown_price_items=baseline_unknown,
        changes=[],
    )

    evaluations = 0
    seen_efts: set[str] = {baseline.eft_text}
    single_swaps: list[tuple[CandidateFit, tuple[str, str, str, str]]] = []  # (cand, (kind, slot, target, repl))
    all_candidates: list[CandidateFit] = []

    def _try_candidate(cand_spec: FitSpec, changes: list[str]) -> CandidateFit | None:
        nonlocal evaluations
        if evaluations >= max_evaluations:
            return None
        cand_eft = eft.export_eft(cand_spec)
        if cand_eft in seen_efts:
            return None
        seen_efts.add(cand_eft)
        evaluations += 1

        val = validate.validate_fit(cand_spec, skills)
        if not val.valid:
            return None

        cost, _, unknown = pricing.estimate_fit_cost(cand_spec, price_provider)
        if profile.budget_cap_isk is not None and (cost is None or cost > profile.budget_cap_isk):
            return None

        stats = engine.calculate(cand_spec, skills)
        score = profiles.score_fit(stats, baseline_stats, cost, baseline_cost, profile)
        cand = CandidateFit(
            fit_spec=cand_spec,
            eft_text=cand_eft,
            stats=stats,
            score=score,
            total_cost_isk=cost,
            unknown_price_items=unknown,
            changes=changes,
        )
        all_candidates.append(cand)
        return cand

    # 1. Single-group module & rig swaps
    seen_slot_mods: set[tuple[str, str]] = set()
    for slot_name in ("high", "med", "low", "rig"):
        for mod in _slot_bucket(fit_spec, slot_name):
            if mod.empty or not mod.name:
                continue
            key = (slot_name, mod.name)
            if key in seen_slot_mods:
                continue
            seen_slot_mods.add(key)

            options = list_legal_variants(mod.name, profile, price_provider)
            for opt in _select_representative_variants(options, mod.name, max_per_slot=4):
                if evaluations >= max_evaluations:
                    break
                swapped = _apply_group_swap(fit_spec, slot_name, mod.name, opt.name)
                cand = _try_candidate(swapped, [f"{mod.name} -> {opt.name}"])
                if cand is not None:
                    single_swaps.append((cand, ("mod", slot_name, mod.name, opt.name)))

            # Also try charge swaps if the module currently has a charge
            if mod.charge and evaluations < max_evaluations:
                charge_items = engine.get_charge_variants(mod.name)
                if charge_items:
                    c_prices = price_provider.get_prices([c.type_id for c in charge_items])
                    allowed_charges: list[ItemInfo] = [
                        c
                        for c in charge_items
                        if c.name != mod.charge
                        and profiles.is_item_allowed_for_profile(c, profile, c_prices.get(c.type_id))
                    ]
                    # Try up to 3 distinct charge alternatives
                    for charge_opt in allowed_charges[:3]:
                        if evaluations >= max_evaluations:
                            break
                        swapped_c = _apply_charge_swap(fit_spec, slot_name, mod.name, charge_opt.name)
                        cand = _try_candidate(
                            swapped_c,
                            [f"{mod.name} charge: {mod.charge} -> {charge_opt.name}"],
                        )
                        if cand is not None:
                            single_swaps.append(
                                (cand, ("charge", slot_name, mod.name, charge_opt.name))
                            )

    # 2. Pairwise combinations of the top single swaps
    single_swaps.sort(key=lambda x: x[0].score, reverse=True)
    top_singles = single_swaps[:6]
    for i in range(len(top_singles)):
        if evaluations >= max_evaluations:
            break
        _, op1 = top_singles[i]
        for j in range(i + 1, len(top_singles)):
            if evaluations >= max_evaluations:
                break
            _, op2 = top_singles[j]
            # Don't combine two swaps on the exact same slot+module target
            if op1[1] == op2[1] and op1[2] == op2[2] and op1[0] == op2[0]:
                continue
            combined = fit_spec
            changes: list[str] = []
            for kind, slot, target, repl in (op1, op2):
                if kind == "mod":
                    combined = _apply_group_swap(combined, slot, target, repl)
                    changes.append(f"{target} -> {repl}")
                else:
                    combined = _apply_charge_swap(combined, slot, target, repl)
                    changes.append(f"{target} charge -> {repl}")
            _try_candidate(combined, changes)

    all_candidates.sort(key=lambda c: c.score, reverse=True)
    return baseline, all_candidates[:top_n]
