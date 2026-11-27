# Changelog — EVE Fitting Advisor

A running record of completed milestones, fixes, and upcoming work.

---

## 2026-11-27 — Profile Enforcement, Drone Validation & Dependency Hygiene

- **Profile enforcement on LLM proposals** (`advisor/profiles.py`, `advisor/llm/advisor.py`, `tests/test_llm.py`):
  - Added `validate_fit_against_profile()` checking published status, `banned_modules`, and `allowed_meta_groups` across fitted modules/rigs and drones/fighters (while allowing faction/T2 charges and `Tech III` subsystems).
  - Exposed `profile_compliant` and `profile_violations` in the `evaluate_fit` tool output and enforced profile compliance in the final proposal filter so disallowed meta-group or banned-module fits are discarded even when within budget.
  - Added `test_advisor_discards_disallowed_meta_group_and_banned_module_proposals`.
- **Explicit drone bandwidth & active-drone validation** (`advisor/engine.py`, `tests/test_validate.py`):
  - Validated `DroneSpec.active` counts in `validate_fit_eos()` (`invalid_active_drones`, `active_drones_exceed_quantity`, `drone_bandwidth_exceeded`, and `active_drones_exceeded`).
  - Added `test_explicit_drone_bandwidth_and_active_counts_validated` covering valid explicit active drones and all four failure cases.
- **Dependency declaration** (`pyproject.toml`, `uv.lock`):
  - Declared `platformdirs>=4.0.0,<5.0.0` in `[project].dependencies` and refreshed `uv.lock` offline without changing any pinned Pyfa dependency versions.
- **Verification**: 64 tests passing (59 core/golden + 5 API) and 0 `ruff` errors.

---

## 2026-11-27 — Advisor, Variant Family & Profile Fixes (`de42abb`)

- **Variation family matching** (`advisor/engine.py`): Switched `get_module_variants()` to use Pyfa's `varParent` / `varChildren` relationships instead of falling back to the whole item group, preventing cross-type/cross-size swaps (e.g. 10MN Afterburner ↔ MWD, or Large ↔ Small/Medium Shield Boosters).
- **Gemini tool-calling fixes** (`advisor/llm/gemini.py`, `advisor/llm/base.py`, `advisor/llm/advisor.py`):
  - Batched consecutive `tool` responses from parallel `functionCall`s into a single `user` turn.
  - Preserved `thoughtSignature` across `functionCall` turns.
  - Sliced tool calls 1-to-1 against `max_tool_calls` before recording the assistant turn.
- **Profiles** (`config/profiles.yaml`): Updated `cheap_fw` budget cap to `50,000,000` ISK and `budget_pvp` budget cap to `120,000,000` ISK; kept `bling` uncapped (`null`). Moved live LLM settings out of `.env.example` into gitignored `.env`.
- **Testing & isolation** (`advisor/pricing.py`, `tests/test_api.py`, `tests/test_phase3.py`, `tests/test_llm.py`, `REVIEW_NOTES.md`):
  - Made `FuzzworkPriceProvider` cache path mockable at runtime and isolated `tests/test_api.py` from live `data/price_cache.json`.
  - Added regression tests `test_module_variants_use_variation_family` and `test_gemini_provider_batches_parallel_tool_responses_and_preserves_thought_signature`.
  - Verified 62 tests passing (57 core/golden + 5 API) and 0 `ruff` errors.

---

## 2026-11-21 — Golden Fit Verification (`5e7ac61`)

- Verified all **10/10 golden fits** in `tests/golden/` against desktop Pyfa `v2.70.0` GUI values (`All V`).
- Added `activate_drones: bool = True` to `advisor/engine.py` and `"drones_active"` flag support in `tests/test_golden.py` so golden tests can match Pyfa's un-launched EFT import state (`0/5`) while the interactive app continues launching drones automatically.
- Added `abs_tol=1.0` specifically on `cap_duration_s` in `tests/test_golden.py` to match Pyfa GUI's integer-second truncation (`%ds` / `%dm%ds`).

---

## 2026-11-21 — Initial Implementation, Phases 1–6 (`3f9b37b`)

- **Engine & DB** (`vendor/pyfa` at `v2.70.0`, `scripts/build_db.py`, `advisor/wxstub.py`, `advisor/engine.py`): Headless `eos` bootstrap with `wx` import stub, `eve.db` builder (`3682781`), explicit `simulateCap()`, and shared-cache in-memory SQLite for FastAPI worker threads.
- **EFT & Validation** (`advisor/eft.py`, `advisor/validate.py`, `advisor/models.py`): Canonical EFT parser/exporter with gamedata-driven slot lookup, T3C subsystems, `/OFFLINE`, empty slots, and full multi-violation legality checker.
- **Variants, Profiles & Pricing** (`advisor/variants.py`, `advisor/profiles.py`, `config/profiles.yaml`, `advisor/pricing.py`): Published-only variant search, 4 profiles (`bling`, `cheap_fw`, `pve`, `budget_pvp`), and Fuzzwork Jita 4-4 market aggregates with 6-hour caching and strict `None` for missing prices.
- **Skills & ESI** (`advisor/skills.py`): `all0`/`all5` presets, type-ID skill mapping, OAuth 2.0 PKCE localhost flow, `X-Compatibility-Date`, `Expires`/`ETag` caching, and `X-ESI-Error-Limit-*` backoff.
- **LLM Advisor** (`advisor/llm/`): `MockLLMProvider`, `GeminiProvider`, and `OpenAICompatProvider` (LM Studio) with tool calling (`evaluate_fit`, `list_variants`, `get_profile`), tool-call capping, and strict engine re-validation.
- **Web UI, CLI & Docs** (`advisor/api.py`, `web/`, `advisor/cli.py`, `REVIEW_NOTES.md`): FastAPI app (`127.0.0.1`), cached CCP image proxy with SVG placeholders, inline SVG fitting wheel UI, CLI (`calc`, `suggest`, `esi-login`), and reviewer notes.

---

## Upcoming / Roadmap

- Verify live Gemini advisor behavior across real player fits and refine prompts or candidate scoring as needed.
- **UI/UX overhaul (`web/`)**: Redesign the fitting workflow, SVG wheel interactions, and advisor comparison layout to be more intuitive.
- Live EVE ESI SSO PKCE test (`python -m advisor.cli esi-login`) once an EVE Developers app is registered.
- Optional active-drone golden verification (Vexor / Tristan with drones launched in Pyfa GUI).
- Finalize and remove `# VERIFY` markers in `config/profiles.yaml`.
