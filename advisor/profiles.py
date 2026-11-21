"""Optimization profile loading and engine-backed fit scoring."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from advisor.models import FitStats, ItemInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILES_PATH = REPO_ROOT / "config" / "profiles.yaml"


class ProfileWeights(BaseModel):
    dps: float = 1.0
    ehp: float = 1.0
    tank: float = 0.5
    speed: float = 0.5
    cap: float = 0.5
    cost: float = 0.0


class ProfileConfig(BaseModel):
    key: str
    name: str
    description: str = ""
    notes: str | None = None
    allowed_meta_groups: list[str] = Field(default_factory=list)
    budget_cap_isk: float | None = None
    banned_modules: list[str] = Field(default_factory=list)
    weights: ProfileWeights = Field(default_factory=ProfileWeights)


def load_profiles(path: Path = DEFAULT_PROFILES_PATH) -> dict[str, ProfileConfig]:
    """Load all optimization profiles from YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles_raw = raw.get("profiles") or {}
    result: dict[str, ProfileConfig] = {}
    for key, cfg in profiles_raw.items():
        data = dict(cfg or {})
        data["key"] = key
        result[key] = ProfileConfig(**data)
    return result


def get_profile(name: str, path: Path = DEFAULT_PROFILES_PATH) -> ProfileConfig:
    """Return a single profile by key, raising KeyError if unknown."""
    profiles = load_profiles(path)
    if name not in profiles:
        available = ", ".join(sorted(profiles))
        raise KeyError(f"Unknown profile {name!r}. Available: {available}")
    return profiles[name]


def classify_meta_group(item: ItemInfo) -> str:
    """Classify an item's effective meta group (distinguishing Meta 1-4 from Tech I)."""
    mg = item.meta_group
    if mg == "Tech I" and item.meta_level is not None and 1 <= item.meta_level <= 4:
        return "Meta"
    return mg


def is_item_allowed_for_profile(
    item: ItemInfo,
    profile: ProfileConfig,
    price_isk: float | None,
) -> bool:
    """Return True if ``item`` satisfies all constraints for ``profile``."""
    if not item.published:
        return False
    if item.name in profile.banned_modules:
        return False
    effective_mg = classify_meta_group(item)
    if profile.allowed_meta_groups and effective_mg not in profile.allowed_meta_groups:
        return False
    # Budget-capped profiles MUST exclude unknown-price items (AGENTS.md §10)
    return not (profile.budget_cap_isk is not None and price_isk is None)


def _rel_ratio(val: float, base: float) -> float:
    if base <= 0:
        return 1.0 if val <= 0 else 1.5
    return val / base


def _cap_score(stats: FitStats) -> float:
    if stats.cap_stable:
        pct = stats.cap_stable_pct if stats.cap_stable_pct is not None else 100.0
        return 1.0 + (pct / 100.0) * 0.5
    dur = stats.cap_duration_s or 0.0
    # Diminishing returns up to 10 minutes
    return min(0.95, dur / 600.0)


def score_fit(
    stats: FitStats,
    baseline: FitStats,
    total_cost: float | None,
    baseline_cost: float | None,
    profile: ProfileConfig,
) -> float:
    """Score a candidate fit relative to ``baseline`` using only engine-computed stats."""
    w = profile.weights

    dps_term = _rel_ratio(stats.dps_total, baseline.dps_total)
    ehp_term = _rel_ratio(stats.ehp_total, baseline.ehp_total)

    cand_tank = stats.tank_passive_shield + stats.tank_shield_repair + stats.tank_armor_repair + stats.tank_hull_repair
    base_tank = baseline.tank_passive_shield + baseline.tank_shield_repair + baseline.tank_armor_repair + baseline.tank_hull_repair
    tank_term = _rel_ratio(cand_tank, base_tank)

    speed_term = _rel_ratio(stats.max_speed, baseline.max_speed)
    cap_term = _cap_score(stats) / max(_cap_score(baseline), 0.1)

    cost_term = 0.0
    if w.cost != 0.0 and total_cost is not None and baseline_cost is not None and baseline_cost > 0:
        cost_term = total_cost / baseline_cost

    return (
        w.dps * dps_term
        + w.ehp * ehp_term
        + w.tank * tank_term
        + w.speed * speed_term
        + w.cap * cap_term
        + w.cost * cost_term
    )
