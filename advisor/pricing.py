"""Market price retrieval and local caching.

Default source: Fuzzwork market aggregates for Jita 4-4 (region 10000002,
station 60003760). Missing market data is ALWAYS ``None`` (unknown), never ``0``.
"""

from __future__ import annotations

import abc
import json
import time
from collections.abc import Iterable
from pathlib import Path

import httpx

from advisor import engine
from advisor.models import FitSpec

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = REPO_ROOT / "data" / "price_cache.json"
DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours

JITA_REGION_ID = 10000002
JITA_STATION_ID = 60003760
FUZZWORK_URL = "https://market.fuzzwork.co.uk/aggregates/"


class PriceProvider(abc.ABC):
    """Swappable interface for EVE item market prices (in ISK)."""

    @abc.abstractmethod
    def get_prices(self, type_ids: Iterable[int]) -> dict[int, float | None]:
        """Return ``{type_id: sell_min_isk_or_None}``.

        Missing prices MUST be ``None``, never ``0``.
        """

    def get_price(self, type_id: int) -> float | None:
        return self.get_prices([type_id]).get(type_id)


class StaticPriceProvider(PriceProvider):
    """In-memory price provider for tests and offline use."""

    def __init__(self, prices: dict[int, float | None]):
        self._prices: dict[int, float | None] = {}
        for tid, val in prices.items():
            # Enforce invariant: 0 or negative is treated as unknown (None)
            self._prices[int(tid)] = float(val) if (val is not None and val > 0) else None

    def get_prices(self, type_ids: Iterable[int]) -> dict[int, float | None]:
        return {int(tid): self._prices.get(int(tid)) for tid in type_ids}


class FuzzworkPriceProvider(PriceProvider):
    """Fetches Jita 4-4 sell-min prices from Fuzzwork with local JSON caching."""

    def __init__(
        self,
        cache_path: Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        station_id: int = JITA_STATION_ID,
        timeout: float = 10.0,
        offline: bool = False,
    ):
        self.cache_path = cache_path if cache_path is not None else DEFAULT_CACHE_PATH
        self.ttl_seconds = ttl_seconds
        self.station_id = station_id
        self.timeout = timeout
        self.offline = offline
        self._cache: dict[str, dict[str, float | None]] = self._load_cache()

    def _load_cache(self) -> dict[str, dict[str, float | None]]:
        if not self.cache_path.is_file():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def get_prices(self, type_ids: Iterable[int]) -> dict[int, float | None]:
        unique_ids = sorted({int(t) for t in type_ids})
        if not unique_ids:
            return {}

        now = time.time()
        results: dict[int, float | None] = {}
        to_fetch: list[int] = []

        for tid in unique_ids:
            entry = self._cache.get(str(tid))
            if entry is not None:
                ts = float(entry.get("ts") or 0.0)
                if now - ts < self.ttl_seconds:
                    val = entry.get("price")
                    results[tid] = float(val) if (val is not None and val > 0) else None
                    continue
            to_fetch.append(tid)

        if to_fetch and not self.offline:
            fetched = self._fetch_from_fuzzwork(to_fetch)
            for tid in to_fetch:
                price = fetched.get(tid)
                results[tid] = price
                self._cache[str(tid)] = {"price": price, "ts": now}
            self._save_cache()
        else:
            for tid in to_fetch:
                entry = self._cache.get(str(tid))
                val = entry.get("price") if entry else None
                results[tid] = float(val) if (val is not None and val > 0) else None

        return results

    def _fetch_from_fuzzwork(self, type_ids: list[int]) -> dict[int, float | None]:
        fetched: dict[int, float | None] = {tid: None for tid in type_ids}
        # Batch requests in chunks of 100 type IDs
        chunk_size = 100
        try:
            with httpx.Client(timeout=self.timeout) as client:
                for i in range(0, len(type_ids), chunk_size):
                    chunk = type_ids[i : i + chunk_size]
                    resp = client.get(
                        FUZZWORK_URL,
                        params={
                            "station": str(self.station_id),
                            "types": ",".join(str(t) for t in chunk),
                        },
                        headers={"User-Agent": "fitting-advisor/0.1 (local EVE fitting tool)"},
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    for tid in chunk:
                        item_data = payload.get(str(tid))
                        if not isinstance(item_data, dict):
                            fetched[tid] = None
                            continue
                        sell = item_data.get("sell") or {}
                        vol = float(sell.get("volume") or 0.0)
                        min_price = float(sell.get("min") or 0.0)
                        # Missing or zero volume/price is ALWAYS None, never 0
                        if vol > 0 and min_price > 0:
                            fetched[tid] = min_price
                        else:
                            fetched[tid] = None
        except httpx.HTTPError:
            # Network failure leaves unfetched items as None (unknown)
            pass
        return fetched


def estimate_fit_cost(
    fit_spec: FitSpec,
    provider: PriceProvider,
) -> tuple[float | None, dict[str, float | None], list[str]]:
    """Estimate total fit cost (hull + fitted modules + drones/fighters).

    Returns ``(total_isk_or_None, per_item_unit_prices, unknown_item_names)``.
    If ANY item has an unknown price (``None``), ``total_isk`` is ``None``.
    """
    counts: dict[str, tuple[int, int]] = {}  # name -> (type_id, qty)

    def _add(name: str, qty: int = 1) -> None:
        info = engine.get_item_info(name)
        if info is None:
            return
        if name in counts:
            tid, old_q = counts[name]
            counts[name] = (tid, old_q + qty)
        else:
            counts[name] = (info.type_id, qty)

    _add(fit_spec.ship, 1)
    for _slot, mod in fit_spec.iter_modules():
        if not mod.empty and mod.name:
            _add(mod.name, 1)
    for drone in fit_spec.drones:
        _add(drone.name, drone.quantity)
    for fighter in fit_spec.fighters:
        _add(fighter.name, fighter.quantity)

    type_ids = [tid for tid, _ in counts.values()]
    raw_prices = provider.get_prices(type_ids)

    item_prices: dict[str, float | None] = {}
    unknown_items: list[str] = []
    total = 0.0

    for name, (tid, qty) in counts.items():
        unit = raw_prices.get(tid)
        item_prices[name] = unit
        if unit is None:
            unknown_items.append(name)
        else:
            total += unit * qty

    if unknown_items:
        return None, item_prices, sorted(unknown_items)
    return total, item_prices, []
