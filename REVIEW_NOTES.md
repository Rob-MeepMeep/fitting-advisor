# REVIEW_NOTES.md — EVE Fitting Advisor

Notes for the code reviewer per `AGENTS.md` §16.

---

## 1. Pyfa Release Tag

- Submodule path: `vendor/pyfa`
- Release tag: `v2.70.0` (commit `e5483f866`)
- Gamedata build version produced from `vendor/pyfa/staticdata`: `3682781`
- No files inside `vendor/pyfa/` were modified.

---

## 2. Reliance on Pyfa Internals Beyond §5 Bootstrap

All `eos` and Pyfa `config` imports are isolated exclusively in `advisor/engine.py` (and `scripts/build_db.py` for running `db_update.py`):

| File | Lines | Pyfa internal used | Why |
| --- | --- | --- | --- |
| `scripts/build_db.py` | 31–37 | `eos.config.gamedata_connectionstring`, `eos.config.saveddata_connectionstring`, `runpy` on `vendor/pyfa/db_update.py` | Redirects `db_update.py` output straight to `data/eve.db` with an in-memory saveddata DB so `vendor/pyfa/` stays clean. |
| `advisor/engine.py` | 73–129 | `config.defPaths(str(pyfa_state_dir))`, shared-cache `saveddata_connectionstring`, and pre-warming `Character.getAll0()` / `getAll5()` | `config.defPaths(None)` defaults to `~/.pyfa`; passing `data/.pyfa_state` keeps Pyfa's internal state inside `data/` (gitignored). Using `sqlite:///file:pyfa_saveddata?mode=memory&cache=shared&uri=true&check_same_thread=False` and pre-warming `All 0`/`All 5` in `init_engine()` allows FastAPI threadpool workers to share the in-memory saveddata DB safely under `_ENGINE_LOCK` without cross-thread SQLite errors or relationship commit warnings. |
| `advisor/engine.py` | 150–175 | `Module.calculateSlot(item)`, `item.metaGroup`, `item.attributes` | Determines canonical slot type, meta group, meta level, rig size, charge size, and volume directly from gamedata. |
| `advisor/engine.py` | 200–206 | `eos.db.gamedata_session.query(Item.typeName)` | Populates published item name cache for "did you mean" suggestions on unknown EFT items (never auto-applied). |
| `advisor/engine.py` | 226–232 | `Character(name, defaultLevel=0, initSkills=False)`, `Character.getSkillIDMap()`, `Skill(char, tid, level)` | Constructs custom skill sets from ESI type-ID maps without touching saveddata DB persistence. Note: `Skill.level` is read-only after init, so skills are constructed via `Skill(char, tid, level)` directly. |
| `advisor/engine.py` | 328–351 | `fit.extraAttributes["maxActiveDrones"]`, `fit.ship.getModifiedItemAttr("droneBandwidth")`, `drone.amountActive` | Automatically activates drones up to ship bandwidth and character max active drones (5 at All V) when EFT lists drone stacks without explicit active counts. |
| `advisor/engine.py` | 388–390 | `fit.simulateCap()` | Explicitly invokes the capacitor simulator after `fit.calculateModifiedAttributes()` so `fit.capStable` / `fit.capState` reflect a real simulation run. |
| `advisor/engine.py` | 405–408, 619–622 | `fit.getSlotsUsed(slot_enum.value)` vs `fit.getNumSlots(slot_enum)` | In Pyfa `eos/saveddata/fit.py`, `getSlotsUsed` compares `mod.slot is type` where `mod.slot` is an `int` (`FittingSlot.*.value`), whereas `fit.slots` keys are `FittingSlot` enum members. |
| `advisor/engine.py` | 640–736 | `fit.canFit(item)`, `mod.isCapitalSize`, `mod.isValidCharge(charge)`, `mod.getModifiedItemAttr("maxGroupOnline" / "maxGroupActive")`, `item.attributes.get("maxGroupFitted")` | Reuses Pyfa's own restriction checks while collecting ALL violations instead of stopping at the first. |
| `advisor/engine.py` | 785–845 | `item.metaParent`, `item.metaGroupItems` / `item.variations`, `eos.db.getGroup(..., eager="items.attributes")`, `mod.getValidCharges()` | Discovers published-only module, rig, and charge variants. |

---

## 3. Deviations from `AGENTS.md`

1. **`advisor/models.py` added**: Extracted plain Pydantic data models (`FitSpec`, `FitStats`, `SkillSet`, `ItemInfo`, `ValidationResult`, etc.) into a dedicated module to avoid circular imports between `engine.py`, `eft.py`, `validate.py`, `pricing.py`, and `api.py`. `advisor/engine.py` remains the only module importing `eos`.
2. **ESI `X-Compatibility-Date` header**: Per CCP's mid-2025 ESI versioning overhaul, `advisor/skills.py` sends `X-Compatibility-Date: 2025-12-16` (overridable via `ESI_COMPATIBILITY_DATE` env var) and calls `/characters/{character_id}/skills/` rather than a legacy `/v4/` path.
3. **LLM HTTP clients via `httpx`**: Both `advisor/llm/gemini.py` and `advisor/llm/openai_compat.py` use `httpx` directly against the Gemini REST API and OpenAI `/chat/completions` API rather than adding vendor SDKs that might conflict with Pyfa's pinned dependencies.
4. **`config.defPaths(str(REPO_ROOT / "data" / ".pyfa_state"))` and shared-cache URI**: Used a workspace-local directory instead of `config.defPaths(None)` so running in sandboxed or CI environments does not require writing to `~/.pyfa`, and used a named shared-cache in-memory SQLite URI (`sqlite:///file:pyfa_saveddata?mode=memory&cache=shared&uri=true&check_same_thread=False`) instead of `"sqlite://"` so FastAPI worker threads can invoke the engine under `_ENGINE_LOCK`.

---

## 4. Assumptions Made

- **Drone activation**: EFT format specifies drone quantities (`Hobgoblin II x5`) but not how many are launched. When `DroneSpec.active` is omitted, `engine.py` launches drones in listed order up to `maxActiveDrones` and `droneBandwidth`.
- **Pricing**: `FuzzworkPriceProvider` uses Jita 4-4 `sell.min` (only when `sell.volume > 0` and `sell.min > 0`; otherwise `None`). Fit cost sums hull + fitted modules + drones + fighters (excluding cargo and loaded ammo charges).
- **Meta group classification**: In EVE's gamedata, Meta 1–4 modules have `metaGroup == "Tech I"` with `metaLevel` 1–4. `advisor/profiles.py` classifies those as `"Meta"` so profiles can distinguish Tech I vs Meta vs Tech II.

---

## 5. `# VERIFY` Items

1. `config/profiles.yaml` (`cheap_fw` profile):
   - FW plex hull-size & meta restrictions (`# VERIFY: user to confirm against current game rules` — plex tiers Scout/Small/Medium/Large/Open and NVY vs ADV rules).
   - `budget_cap_isk: 25000000` (`# VERIFY: user to confirm against current game rules` — typical cheap FW frigate/destroyer budget target).
2. `advisor/skills.py`:
   - `DEFAULT_COMPATIBILITY_DATE = "2025-12-16"` — verify against the latest live ESI compatibility date if you test ESI SSO against Tranquility.

---

## 6. Golden Test Status

All 10 golden fits in `tests/golden/*.eft` now have user-verified Pyfa `v2.70.0` GUI values in `tests/golden/*.expected.json` (10/10 passing, 0 skipped). No expected values were invented or modified:

- **Drone activation difference on EFT import**: Desktop Pyfa leaves imported drones inactive (`0/5`) until the user launches them, whereas `advisor/engine.py` defaults to `activate_drones=True` so interactive/advisor evaluation reflects launched drone DPS. To compare directly against Pyfa's un-launched import state without discarding real drone DPS in the app, the 6 drone-bearing golden JSON files (`apocalypse_battleship`, `caracal_missiles`, `drake_shield`, `maller_armor`, `tristan_empty_slots`, `vexor_drones`) set `"drones_active": false` and `test_golden.py` passes `activate_drones=False` when present.
- **Cap duration GUI truncation**: Pyfa's GUI (`gui/builtinStatsViews/capacitorViewFull.py:165-168`) truncates `fit.capState` to whole seconds (`%ds` / `%dm%ds`), so `test_golden.py` uses `abs_tol=1.0` specifically on `cap_duration_s` while keeping strict `0.5%` relative tolerance on all other fields.

Verified golden fits:
- `apocalypse_battleship.eft` — **verified** (All V, `drones_active: false`).
- `caracal_missiles.eft` — **verified** (All V, `drones_active: false`).
- `drake_shield.eft` — **verified** (All V, `drones_active: false`).
- `maller_armor.eft` — **verified** (All V, `drones_active: false`).
- `rifter_turrets.eft` — **verified** (All V, cap unstable `22.5s` / Pyfa `Lasts 22s`).
- `tengu_t3c.eft` — **verified** (All V, 4 subsystems).
- `thrasher_offline.eft` — **verified** (All V, `/OFFLINE` module).
- `tristan_empty_slots.eft` — **verified** (All V, empty slots, `drones_active: false`).
- `vexor_drones.eft` — **verified** (All V, `drones_active: false`).
- `rifter_illegal.eft` — **verified** (`expected_valid: false`, failing with `cpu_exceeded`, `pg_exceeded`, `rig_size_mismatch`, `max_group_fitted_exceeded`, `invalid_charge`).

---

## 7. Known Limitations & Uncertainties

- **Drone boat active-DPS GUI cross-check**: The 6 drone-bearing golden fits were verified against Pyfa's initial import state (`0/5` drones launched). Active-drone stats (`activate_drones=True`) have not yet been cross-checked against Pyfa with drones toggled active in the GUI.
- **Live Gemini / LM Studio / ESI SSO**: Unit tests cover the advisor loop, tool-call cap, invalid-proposal rejection, and ESI PKCE/caching/error-limit logic via mocks/MockTransport without live network credentials. Live end-to-end verification against Gemini, LM Studio, and Tranquility ESI requires user credentials in `.env`.
- **Variant grouping**: `get_module_variants` falls back to same-group + matching slot/rigSize/chargeSize/capital-size when `metaParent` links are absent; this can occasionally include related modules in the same group (e.g., 1MN Afterburner alongside 5MN Microwarpdrive, since both are in the Propulsion Module group).
- **Citadels / Structures & T3D modes**: Basic ship and T3C subsystem support is implemented and tested; Upwell structures and Tactical Destroyer modes are not yet exposed in the UI or EFT parser.
