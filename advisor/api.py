"""FastAPI backend serving JSON APIs, image proxy cache, and the static web UI.

Binds strictly to 127.0.0.1. Never logs API keys or ESI tokens.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from advisor import eft, engine, pricing, profiles, skills, validate
from advisor.llm import advisor as llm_advisor
from advisor.models import FitStats, SkillSet, ValidationResult

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
IMAGE_CACHE_DIR = REPO_ROOT / "data" / "image_cache"

app = FastAPI(title="EVE Fitting Advisor", version="0.1.0")

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

_PLACEHOLDER_RENDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect width="256" height="256" fill="#0b111b"/>
  <circle cx="128" cy="128" r="96" fill="none" stroke="#24354d" stroke-width="2" stroke-dasharray="6 6"/>
  <polygon points="128,62 178,162 128,140 78,162" fill="#1e3148" stroke="#4cc9f0" stroke-width="2"/>
  <text x="128" y="205" text-anchor="middle" fill="#7c96b3" font-family="sans-serif" font-size="13">SHIP RENDER</text>
</svg>"""

_PLACEHOLDER_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="8" fill="#131c2b" stroke="#2c3e58" stroke-width="2"/>
  <circle cx="32" cy="32" r="14" fill="none" stroke="#4cc9f0" stroke-width="2"/>
  <circle cx="32" cy="32" r="4" fill="#4cc9f0"/>
</svg>"""


def _resolve_skillset(preset: str, character_id: int | None) -> SkillSet:
    if character_id is not None:
        store = skills.TokenStore()
        tok = store.get(character_id)
        if tok is None:
            raise HTTPException(status_code=404, detail=f"Character {character_id} not found in local ESI store")
        return SkillSet(preset="custom", character_name=tok.character_name, skills=dict(tok.skills))
    try:
        return skills.get_preset(preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CalculateRequest(BaseModel):
    eft: str
    skills_preset: str = "all5"
    character_id: int | None = None
    offline_prices: bool = False


class CalculateResponse(BaseModel):
    eft_canonical: str
    validation: ValidationResult
    stats: FitStats
    total_cost_isk: float | None = None
    unknown_price_items: list[str] = Field(default_factory=list)


class AdviseRequest(BaseModel):
    eft: str
    profile: str = "pve"
    skills_preset: str = "all5"
    character_id: int | None = None
    max_evaluations: int = Field(default=40, ge=1, le=200)
    offline_prices: bool = False


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/api/meta")
def api_meta() -> dict:
    versions = engine.get_versions()
    loaded_profiles = profiles.load_profiles()
    token_store = skills.TokenStore()
    return {
        "pyfa_version": versions["pyfa_version"],
        "gamedata_version": versions["gamedata_version"],
        "profiles": [
            {
                "key": p.key,
                "name": p.name,
                "description": p.description,
                "budget_cap_isk": p.budget_cap_isk,
                "allowed_meta_groups": p.allowed_meta_groups,
            }
            for p in loaded_profiles.values()
        ],
        "llm_provider": (os.environ.get("LLM_PROVIDER") or "mock").strip().lower(),
        "esi_configured": bool(os.environ.get("ESI_CLIENT_ID")),
        "characters": token_store.list_characters(),
    }


@app.post("/api/calculate", response_model=CalculateResponse)
def api_calculate(req: CalculateRequest) -> CalculateResponse:
    try:
        fit_spec = eft.parse_eft(req.eft)
    except eft.EFTParseError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "line_number": exc.line_number,
                "suggestions": exc.suggestions,
            },
        ) from exc

    skillset = _resolve_skillset(req.skills_preset, req.character_id)
    val = validate.validate_fit(fit_spec, skillset)
    stats = engine.calculate(fit_spec, skillset)

    price_provider = pricing.FuzzworkPriceProvider(offline=req.offline_prices)
    total_cost, _, unknown = pricing.estimate_fit_cost(fit_spec, price_provider)

    return CalculateResponse(
        eft_canonical=eft.export_eft(fit_spec),
        validation=val,
        stats=stats,
        total_cost_isk=total_cost,
        unknown_price_items=unknown,
    )


@app.post("/api/advise", response_model=llm_advisor.AdvisorResponse)
def api_advise(req: AdviseRequest) -> llm_advisor.AdvisorResponse:
    try:
        fit_spec = eft.parse_eft(req.eft)
    except eft.EFTParseError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "line_number": exc.line_number,
                "suggestions": exc.suggestions,
            },
        ) from exc

    try:
        profile = profiles.get_profile(req.profile)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    skillset = _resolve_skillset(req.skills_preset, req.character_id)
    price_provider = pricing.FuzzworkPriceProvider(offline=req.offline_prices)
    provider = llm_advisor.get_provider_from_env()

    return llm_advisor.advise_fit(
        fit_spec=fit_spec,
        profile=profile,
        price_provider=price_provider,
        provider=provider,
        skills=skillset,
        max_evaluations=req.max_evaluations,
    )


@app.get("/api/images/{kind}/{type_id}")
def api_image_proxy(kind: Literal["render", "icon"], type_id: int) -> Response:
    """Proxy and cache CCP image server assets locally; return placeholder SVG if unavailable."""
    if type_id <= 0:
        svg = _PLACEHOLDER_RENDER_SVG if kind == "render" else _PLACEHOLDER_ICON_SVG
        return Response(content=svg, media_type="image/svg+xml")

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = IMAGE_CACHE_DIR / f"{kind}_{type_id}.png"

    if cache_file.is_file() and cache_file.stat().st_size > 0:
        return Response(
            content=cache_file.read_bytes(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    size = 512 if kind == "render" else 64
    remote_url = f"https://images.evetech.net/types/{type_id}/{kind}?size={size}"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                remote_url,
                headers={"User-Agent": "fitting-advisor/0.1 (local EVE fitting tool)"},
            )
            if resp.status_code == 200 and resp.content:
                cache_file.write_bytes(resp.content)
                return Response(
                    content=resp.content,
                    media_type=resp.headers.get("content-type", "image/png"),
                    headers={"Cache-Control": "public, max-age=86400"},
                )
    except httpx.HTTPError:
        pass

    svg = _PLACEHOLDER_RENDER_SVG if kind == "render" else _PLACEHOLDER_ICON_SVG
    return Response(content=svg, media_type="image/svg+xml")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("advisor.api:app", host="127.0.0.1", port=8000, reload=False)
