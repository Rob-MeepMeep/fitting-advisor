# AGENTS.md — EVE Fitting Advisor (working name: `fitting-advisor`)

Instructions for the coding agent building this project. Read the whole file before writing any code. Sections marked **⚠ MISTAKE TRAP** describe errors that are easy to make and have either already happened during feasibility testing or are known failure modes. Treat them as hard rules.

---

## 1. What we are building

A standalone local application that:

1. Takes an EVE Online ship fit (EFT format) and calculates its stats using **Pyfa's `eos` engine**, run headless (no Pyfa GUI).
2. Displays the fit visually (an in-game-style fitting wheel in SVG) with clean stats.
3. Generates alternative fits for selectable **profiles** (e.g. Bling, Cheap FW, PvE) by swapping modules, ammo and rigs, and scores them.
4. Uses an LLM (Gemini, or a local OpenAI-compatible model via LM Studio) to **explain** the trade-offs and optionally propose swaps. **Every LLM-proposed fit is re-validated by the engine before it is shown.**
5. Imports and exports EFT strings.
6. Uses character skills from ESI (EVE SSO), with "All 0" and "All V" presets.

### Core principle (non-negotiable)

**The engine does all the maths. The LLM never calculates, estimates or asserts a stat or a fit's legality.** If you find yourself writing any formula for DPS, EHP, resists, stacking penalties, capacitor or speed, stop. That number must come from `eos`.

---

## 2. Stack

- Python 3.12. Use `uv` for environment and dependency management (Pyfa already uses it).
- Backend: FastAPI, serving JSON and the static front end on `localhost`.
- Front end: plain HTML, CSS and JavaScript with inline SVG. **No build step, no framework.**
- Tests: `pytest`.
- Paths: always `pathlib`. Must work on Windows and Linux. Do not hardcode separators or home directories.

### ⚠ MISTAKE TRAP: dependency pins
Pyfa pins exact versions (numpy, sqlalchemy, cryptography and others) in its `pyproject.toml`. Our dependencies must be compatible with those pins. Do not upgrade any package Pyfa pins. If a package you want conflicts, choose a different package.

---

## 3. Repository layout

```
fitting-advisor/
├── AGENTS.md
├── README.md
├── LICENSE                  # GPLv3 (required because we use Pyfa's code)
├── pyproject.toml
├── .gitignore               # must include: .env, data/, *.db, __pycache__/, .venv/
├── .env.example             # variable names only, no values
├── vendor/pyfa/             # git submodule, pinned to a release tag
├── scripts/
│   └── build_db.py          # builds Pyfa's eve.db headlessly
├── advisor/
│   ├── __init__.py
│   ├── wxstub.py            # wx import stub (see §5)
│   ├── engine.py            # the ONLY module that imports eos
│   ├── eft.py               # EFT parser and exporter
│   ├── validate.py          # fit legality checks
│   ├── variants.py          # candidate generation
│   ├── profiles.py          # profile loading and scoring
│   ├── pricing.py           # market prices with caching
│   ├── skills.py            # ESI SSO and skill import
│   ├── llm/
│   │   ├── base.py          # provider interface
│   │   ├── gemini.py
│   │   ├── openai_compat.py # LM Studio and other OpenAI-compatible endpoints
│   │   └── advisor.py       # tool-calling loop and validation
│   └── api.py               # FastAPI app
├── config/
│   └── profiles.yaml
├── web/                     # static front end
└── tests/
    ├── golden/              # reference fits and expected stats (user-supplied values)
    └── ...
```

---

## 4. Pyfa setup

1. Add Pyfa as a **git submodule** at `vendor/pyfa`, checked out at the **latest release tag** (not `master`). Record the tag in `README.md`.
2. **Never edit files inside `vendor/pyfa`.** All workarounds live in `advisor/`. Updating Pyfa must be a matter of moving the tag and running the tests.
3. `eve.db` is built from Pyfa's bundled `staticdata/` folder by `vendor/pyfa/db_update.py`. **Do not download CCP's SDE.** It isn't needed.
4. `scripts/build_db.py` must install the wx stub (§5) and then run Pyfa's `db_update.py` via `runpy`. Output goes to `data/eve.db`, which is gitignored and never committed (it is large).
5. Expose the Pyfa version and game data version through the API, and show both in the UI footer.

---

## 5. Running `eos` headless

This was tested and works. Follow this approach exactly.

### ⚠ MISTAKE TRAP: wxPython
Pyfa's `config.py` imports `wx`, so even the engine won't load without it. **Do not install wxPython.** Instead, `advisor/wxstub.py` registers a `sys.meta_path` finder that returns a permissive dummy module for `wx` and every `wx.*` submodule. It must be installed **before** anything from Pyfa is imported. Do not put it in a global `sitecustomize.py`, because that would affect every Python process.

Reference implementation (proven during testing):

```python
import sys, types, importlib.abc, importlib.machinery

class _Any:
    def __init__(s, *a, **k): pass
    def __call__(s, *a, **k): return _Any()
    def __getattr__(s, n):
        if n.startswith('__'): raise AttributeError(n)
        return _Any()
    def __bool__(s): return False
    def __iter__(s): return iter(())
    def __mro_entries__(s, bases): return (object,)
    def __or__(s, o): return s
    __ror__ = __or__

class _Mod(types.ModuleType):
    def __getattr__(s, n):
        if n.startswith('__'): raise AttributeError(n)
        if n in ('NewEvent', 'NewCommandEvent'): return lambda: (_Any, _Any())
        return _Any()

class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(s, name, path, target=None):
        if name == 'wx' or name.startswith('wx.'):
            return importlib.machinery.ModuleSpec(name, s, is_package=True)
    def create_module(s, spec):
        m = _Mod(spec.name); m.__path__ = []; return m
    def exec_module(s, m): pass

def install():
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
```

### ⚠ MISTAKE TRAP: Pyfa's service and GUI layers
**Do not import anything from `service.*` or `gui.*`.** `service.port.eft` (Pyfa's EFT importer) pulls in `gui.mainFrame` through a circular import and fails even with the wx stub. Use only `eos.*` and `config`. This is why we write our own EFT parser (§6).

### Working engine bootstrap (proven during testing)

```python
import config
config.defPaths(None)
config.gamedata_connectionstring = f'sqlite:///{EVE_DB_PATH}'
config.saveddata_connectionstring = 'sqlite://'        # in-memory; we don't persist Pyfa fits
import eos.db
eos.db.saveddata_meta.create_all(eos.db.saveddata_engine)  # SQLAlchemy 2.x: bind is REQUIRED
from eos.saveddata.fit import Fit
from eos.saveddata.ship import Ship
from eos.saveddata.module import Module
from eos.saveddata.character import Character
from eos.const import FittingModuleState
```

### ⚠ MISTAKE TRAP: things `eos` does not do for you
These were all found during testing:

- **Module owner must be set manually:** `m.owner = fit` for every module. Without it, DPS calculation crashes with `'NoneType' object has no attribute 'factorReload'`.
- **Module state must be set explicitly.** Use `ACTIVE` when `m.isValidState(FittingModuleState.ACTIVE)` is true, otherwise `ONLINE`. Respect `/OFFLINE` from the EFT string.
- **Appending to `fit.modules` performs no legality checks.** During testing, a hand-written Rifter fit calculated without complaint at 171/162 CPU and 500/400 calibration. Legality is our job (§7).
- **Capacitor stability:** a fresh test reported `capStable == True` for a Rifter with an active MWD, which is suspicious. Investigate how Pyfa triggers its capacitor simulation (look in `eos/capSim.py` and how the fit uses it) and make sure the reported cap figures come from an actual simulation run. Verify against the Pyfa GUI before marking this done.

All of this belongs in `advisor/engine.py`, which is the **only** module allowed to import `eos`. Everything else talks to it through a small interface you own, for example:

```python
def calculate(fit_spec: FitSpec, skills: SkillSet) -> FitStats: ...
```

`FitSpec` and `FitStats` are plain dataclasses (or Pydantic models) with no `eos` types in them.

---

## 6. EFT parsing and export

Write our own parser and exporter in `advisor/eft.py`.

Format rules:

- Header: `[Ship Type, Fit Name]`
- Sections are separated by blank lines, in this order: low slots, mid slots, high slots, rigs, subsystems, then drones/fighters/cargo.
- A charge follows the module after a comma: `200mm AutoCannon II, Republic Fleet EMP S`
- An offline module ends with ` /OFFLINE`.
- Empty slots appear as `[Empty Low slot]`, `[Empty Med slot]`, `[Empty High slot]`, `[Empty Rig slot]`, `[Empty Subsystem slot]`.
- Drones and cargo use quantity suffixes: `Hobgoblin II x5`.

### ⚠ MISTAKE TRAPS: EFT
- **Do not assume section order alone tells you the slot type.** Real-world EFT strings are sometimes missing sections or contain extra blank lines. Determine each module's slot from the item's data in `eos` (its slot type), and use section order only as a fallback.
- **Item names must match exactly** through `eos.db.getItem`. Unknown names produce a clear error naming the line. **No fuzzy matching or silent substitution.** If you add a "did you mean" helper, it may only suggest, never auto-apply.
- Strategic cruisers need subsystems before slot counts are correct. Handle T3 cruisers and test one.
- **Round-trip test:** for every golden fit, `export(parse(text))` must produce an equivalent fit. Equivalent means the same modules, charges, states and quantities; whitespace may differ.

---

## 7. Validation (`advisor/validate.py`)

A fit is legal only if all of these hold:

- CPU used ≤ CPU output, and PG used ≤ PG output (both as modified by skills and modules).
- Calibration used ≤ calibration capacity.
- The number of modules per slot type ≤ the ship's slot counts (after subsystems).
- Turret and launcher modules ≤ the ship's turret and launcher hardpoints.
- Rig size matches the ship's rig size.
- Max-group-fitted and max-group-active limits are respected (e.g. one Damage Control).
- Ship-type and ship-group restrictions on modules are respected.
- Each charge is valid for its module (charge group and size).

Prefer the engine's own attributes and checks wherever they exist. Search `eos` for existing validity helpers before writing your own. Return **all** violations, not just the first, each as a structured, human-readable reason.

---

## 8. Golden tests

### ⚠ MISTAKE TRAP: the most important rule in this file
**Expected values in `tests/golden/` come from the user, read off the Pyfa GUI. You must never invent expected values, and never change an expected value to make a test pass.** If a golden test fails, the code is wrong or the test reveals a genuine discrepancy. Investigate and report; do not "fix" the assertion.

Structure:

- `tests/golden/<name>.eft`: the fit.
- `tests/golden/<name>.expected.json`: values supplied by the user, plus the skill preset used (`all5` by default) and the Pyfa version they were read from.

If an `.expected.json` doesn't exist yet, create the `.eft` with a matching `.expected.json` stub containing `"status": "awaiting_user_values"`, and have the test **skip with that reason** rather than pass or fail.

Tolerance: relative 0.5%, because the Pyfa GUI displays rounded figures. Compare at least: total DPS, volley, EHP per layer and total, CPU and PG used, calibration used, max speed, align time, and cap stability (stable/unstable, plus duration when unstable).

Provide at least 10 golden fits covering: a frigate with turrets, a missile boat, a drone boat, an armour brick, a shield buster, a T3 cruiser with subsystems, a fit with an offline module, a fit with empty slots, a capital or large ship, and one deliberately illegal fit (expected to fail validation with specific reasons).

---

## 9. Candidate generation and profiles

### Variants (`advisor/variants.py`)
For each fitted module, its variants are items in the same group (or the same variation family, if the data exposes one), across meta groups: Tech I, meta, Tech II, faction, deadspace, officer. Do the same for charges (same charge group and size) and rigs (same size).

### ⚠ MISTAKE TRAP: unpublished items
The database contains unpublished, test and deprecated items. **Only ever use items where `published` is true.** Write a test that fails if any candidate is unpublished.

### Search
Do not brute-force every combination; that grows combinatorially. Use a bounded approach, such as greedy single swaps followed by limited pairwise swaps, with a configurable cap on the number of evaluations. Every candidate goes through `validate` and then `engine.calculate`. Discard illegal candidates.

### Profiles (`config/profiles.yaml`)
Each profile defines constraints (allowed meta groups, budget cap, banned modules) and scoring weights (DPS, EHP, speed, cap, cost). Initial profiles:

- `bling`: all meta groups, no budget cap, maximise output.
- `cheap_fw`: budget cap, faction warfare oriented.
- `pve`: sustained DPS, cap stability and tank weighted.
- `budget_pvp`: value for money.

### ⚠ MISTAKE TRAP: game rules from memory
Do not hardcode game rules from memory, such as faction warfare plex ship-size limits. Put them in `profiles.yaml`, marked `# VERIFY: user to confirm against current game rules`, and list them in `REVIEW_NOTES.md`.

---

## 10. Pricing (`advisor/pricing.py`)

- Source: Fuzzwork market aggregates for Jita (region `10000002`, station `60003760`). Make the source swappable behind an interface.
- Cache prices locally with a timestamp. Refresh no more than every 6 hours by default.

### ⚠ MISTAKE TRAP: missing prices
Officer and deadspace items often have thin or missing market data. **A missing price is `None` ("unknown"), never `0`.** If it defaults to 0, the "cheap" profile will happily recommend officer modules. Profiles with a budget cap must exclude unknown-price items, and the UI must show "price unknown". Write a test for this.

---

## 11. Skills (`advisor/skills.py`)

- Presets: `Character.getAll0()` and `Character.getAll5()` from `eos`.
- ESI import: EVE SSO with OAuth 2.0 **PKCE** (this is a desktop app with no client secret). Scope: `esi-skills.read_skills.v1`. The callback runs on a localhost port.
- The user registers the application on the EVE developers site and puts the client ID in `.env`. Read it from the environment.
- Store tokens in the user's local app data directory (via `pathlib` and the platform's convention), **never in the repo**.
- Map skills by **type ID**, not by name.
- Send a descriptive `User-Agent` with contact details on every ESI request, respect ESI cache headers, and back off on error-limit headers. Check the current ESI documentation for versioning and header requirements rather than assuming.

---

## 12. LLM layer (`advisor/llm/`)

- `base.py` defines a provider interface: a chat call with tool calling and structured (JSON) output.
- Two implementations: `gemini.py` (Gemini API) and `openai_compat.py` (any OpenAI-compatible endpoint, such as LM Studio at a configurable base URL).
- Provider, model name, base URL and API key all come from `.env`. Nothing is hardcoded.

### Tools exposed to the model
- `evaluate_fit(eft: str) -> stats JSON or validation errors`
- `list_variants(module_name: str, profile: str) -> legal variants with price and meta group`
- `get_profile(name: str) -> constraints and weights`

### The advisor loop (`advisor.py`)
1. Compute the original fit's stats and the top N candidates from `variants.py` **before** calling the LLM, and pass them in as structured data.
2. The model explains trade-offs and may propose fits via `evaluate_fit`.
3. **Any fit the model proposes that fails validation or doesn't parse is discarded, never shown.** Log it.
4. Cap the loop at a configurable number of tool calls (default 8).
5. The final response is structured JSON: a summary, a list of recommended fits (as EFT, each with engine-computed stats attached by our code, **not** by the model), and the reasoning for each.

### ⚠ MISTAKE TRAP: model-supplied numbers
Never display a number the model wrote. Stats shown in the UI come only from `engine.calculate`. If the model's prose quotes a figure, that's acceptable only as narrative; the stats panel uses engine output.

### Mock provider
Add a `mock` provider that returns canned responses, including some **deliberately invalid** proposals (bad module names, over-CPU fits). Test that the advisor loop discards them. All LLM-layer tests must run against the mock without network access.

---

## 13. Front end (`web/`)

- An in-game-style fitting wheel in SVG: the ship render in the centre, slot arcs (high, mid, low, rig) around it, and CPU, PG and calibration bars.
- Images from CCP's image server: `https://images.evetech.net/types/{type_id}/render?size=512` for ships and `.../icon?size=64` for modules. Proxy and cache them through the backend so the front end doesn't depend on the network after the first load. Show a placeholder when an image is unavailable.
- Views: fit input (paste EFT), stats panel, profile selector, advisor results with side-by-side comparison against the original, and EFT export (copy to clipboard).
- Show validation errors clearly, listing every violation.
- Footer: Pyfa version and game data version.

---

## 14. Security and hygiene

- Secrets live only in `.env`, which is gitignored. Provide `.env.example` with variable names only.
- Never log API keys or ESI tokens.
- The server binds to `127.0.0.1` only.
- Do not commit `eve.db`, caches or tokens.

---

## 15. Phases and gates

Complete the phases in order. **Do not start a phase until every test from the previous phases passes** (golden tests awaiting user values may be skipped).

1. Repo scaffold, Pyfa submodule, `build_db.py`, wx stub, `engine.py`, plus a CLI: `python -m advisor.cli calc fit.eft` prints the stats.
2. `eft.py` (parse, export, round-trip) and `validate.py`, with golden fit files and expected-value stubs.
3. `variants.py`, `profiles.py`, `pricing.py` and a CLI command: `python -m advisor.cli suggest fit.eft --profile pve`.
4. `skills.py` with presets first, then ESI SSO.
5. LLM layer with the mock provider first, then Gemini and OpenAI-compatible.
6. FastAPI and the front end.

---

## 16. When you're finished: `REVIEW_NOTES.md`

Create `REVIEW_NOTES.md` in the repo root for the code reviewer, containing:

- The Pyfa release tag used.
- Every place where the code relies on Pyfa internals beyond the bootstrap in §5 (file, line, and why).
- Every deviation from this file, with the reason.
- Every assumption you made.
- Every `# VERIFY` item.
- Golden test status: which have user values, which are awaiting values, and any failures with your investigation notes.
- Known limitations and anything you were unsure about.

Be honest in this file. An accurate list of problems is worth more than a clean-looking report.
