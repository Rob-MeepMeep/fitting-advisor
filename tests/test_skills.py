"""Phase 4 tests: skill presets, custom type-ID skill mapping, PKCE & ESI client."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest

from advisor import eft, engine, skills
from advisor.models import SkillSet

GOLDEN_DIR = Path(__file__).parent / "golden"


def _make_fake_jwt(char_id: int, char_name: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": f"CHARACTER:EVE:{char_id}", "name": char_name}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def test_presets_and_custom_skillset_affect_engine():
    fit = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    s0 = engine.calculate(fit, skills.all0_skillset())
    s5 = engine.calculate(fit, skills.all5_skillset())
    assert s5.dps_total > s0.dps_total

    # Custom character with CPU Management (typeID 3426) = 5, all others 0
    custom = SkillSet(preset="custom", character_name="Test Pilot", skills={3426: 5})
    sc = engine.calculate(fit, custom)
    assert sc.cpu_output == pytest.approx(s5.cpu_output, rel=1e-4)
    assert sc.dps_total == pytest.approx(s0.dps_total, rel=1e-4)


def test_pkce_generation_and_authorize_url():
    verifier, challenge = skills.generate_pkce_pair()
    assert len(verifier) >= 43
    assert "=" not in challenge
    url = skills.build_authorize_url(
        client_id="test-client",
        redirect_uri="http://127.0.0.1:8765/callback",
        code_challenge=challenge,
        state="xyz123",
    )
    assert "code_challenge_method=S256" in url
    assert "esi-skills.read_skills.v1" in url
    assert "client_id=test-client" in url


def test_esi_client_skills_fetch_caching_and_error_limit(tmp_path: Path):
    calls: list[httpx.Request] = []
    jwt_token = _make_fake_jwt(90000001, "Capsuleer Test")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": jwt_token,
                    "refresh_token": "ref-123",
                    "expires_in": 1200,
                },
            )
        if request.url.path.endswith("/characters/90000001/skills/"):
            assert request.headers.get("X-Compatibility-Date") == skills.DEFAULT_COMPATIBILITY_DATE
            assert "fitting-advisor" in (request.headers.get("User-Agent") or "")
            return httpx.Response(
                200,
                headers={
                    "ETag": '"etag-1"',
                    "Expires": "Wed, 21 Oct 2099 07:28:00 GMT",
                    "X-ESI-Error-Limit-Remain": "95",
                    "X-ESI-Error-Limit-Reset": "60",
                },
                json={
                    "skills": [
                        {"skill_id": 3426, "active_skill_level": 5, "trained_skill_level": 5},
                        {"skill_id": 3300, "active_skill_level": 4, "trained_skill_level": 4},
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    store = skills.TokenStore(storage_dir=tmp_path)
    esi = skills.ESIClient(client_id="cid", token_store=store, http_client=http_client)

    tok = esi.exchange_code(code="auth-code", code_verifier="verifier")
    assert tok.character_id == 90000001
    assert tok.character_name == "Capsuleer Test"

    # First fetch hits ESI
    ss1 = esi.fetch_skills(90000001)
    assert ss1.skills == {3426: 5, 3300: 4}
    assert len(calls) == 2  # token + skills

    # Second fetch respects Expires header and does NOT hit ESI again
    ss2 = esi.fetch_skills(90000001)
    assert ss2.skills == {3426: 5, 3300: 4}
    assert len(calls) == 2

    # Error limit backoff test
    esi._error_limit_remain = 2
    esi._error_limit_reset_at = time.time() + 30
    stored = store.get(90000001)
    assert stored is not None
    stored.skills_expires_at = 0.0  # force cache expiry
    store.save(stored)
    with pytest.raises(skills.ESIErrorLimitExceeded):
        esi.fetch_skills(90000001)
