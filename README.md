# EVE Fitting Advisor (`fitting-advisor`)

A standalone local application for analysing and optimising EVE Online ship fits using Pyfa's `eos` engine headlessly, with an SVG fitting wheel UI, profile-based candidate generation, ESI skill import, and optional LLM explanation/advice.

## Pyfa Version

- Submodule: `vendor/pyfa`
- Release tag: `v2.70.0`

## Setup

1. Clone with submodules:
   ```bash
   git clone --recurse-submodules <repo-url>
   cd fitting-advisor
   ```
2. Install dependencies with `uv`:
   ```bash
   uv sync
   ```
3. Build Pyfa's game database (`data/eve.db`):
   ```bash
   uv run python scripts/build_db.py
   ```
4. Copy `.env.example` to `.env` and fill in any LLM or ESI settings you want to use.

## CLI

```bash
uv run python -m advisor.cli calc path/to/fit.eft
uv run python -m advisor.cli suggest path/to/fit.eft --profile pve
```

## Web UI

```bash
uv run uvicorn advisor.api:app --host 127.0.0.1 --port 8000
```

## License

GPLv3 (see `LICENSE`).
