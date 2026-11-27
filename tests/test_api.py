"""Phase 6 tests: FastAPI endpoints, image placeholder fallback, and static UI serving."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from advisor import pricing
from advisor.api import app

GOLDEN_DIR = Path(__file__).parent / "golden"
client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_price_cache(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pricing, "DEFAULT_CACHE_PATH", tmp_path / "price_cache.json")


def test_index_and_meta():
    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "EVE Fitting Advisor" in r_index.text

    r_meta = client.get("/api/meta")
    assert r_meta.status_code == 200
    meta = r_meta.json()
    assert meta["pyfa_version"] != "unknown"
    assert meta["gamedata_version"] != "unknown"
    profile_keys = {p["key"] for p in meta["profiles"]}
    assert {"bling", "cheap_fw", "pve", "budget_pvp"}.issubset(profile_keys)


def test_calculate_legal_and_illegal_fits():
    legal_eft = (GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8")
    r_ok = client.post("/api/calculate", json={"eft": legal_eft, "offline_prices": True})
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert data["validation"]["valid"] is True
    assert data["stats"]["ship"] == "Rifter"
    assert data["stats"]["dps_total"] > 100.0
    assert data["total_cost_isk"] is None  # offline prices -> unknown (never 0)

    illegal_eft = (GOLDEN_DIR / "rifter_illegal.eft").read_text(encoding="utf-8")
    r_ill = client.post("/api/calculate", json={"eft": illegal_eft, "offline_prices": True})
    assert r_ill.status_code == 200
    ill_data = r_ill.json()
    assert ill_data["validation"]["valid"] is False
    assert len(ill_data["validation"]["issues"]) >= 4


def test_calculate_unknown_item_returns_400():
    r = client.post(
        "/api/calculate",
        json={"eft": "[Rifter, Bad]\nNonexistent Module XYZ\n", "offline_prices": True},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["line_number"] == 2


def test_advise_endpoint_with_mock_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    legal_eft = (GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8")
    r = client.post(
        "/api/advise",
        json={
            "eft": legal_eft,
            "profile": "bling",
            "max_evaluations": 10,
            "offline_prices": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["profile"] == "bling"
    assert data["summary"]
    assert data["baseline_stats"]["ship"] == "Rifter"
    assert len(data["recommendations"]) >= 1


def test_image_proxy_placeholder_for_invalid_type():
    r = client.get("/api/images/render/0")
    assert r.status_code == 200
    assert "image/svg+xml" in r.headers["content-type"]
