"""Command-line interface for EVE Fitting Advisor."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv

from advisor import eft, engine, pricing, profiles, skills, variants
from advisor.models import FitStats, SkillSet


def _format_duration(seconds: float) -> str:
    total = round(seconds)
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m {secs}s"
    if mins:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _format_isk(value: float | None) -> str:
    if value is None:
        return "price unknown"
    if value >= 1e9:
        return f"{value / 1e9:.2f}B ISK"
    if value >= 1e6:
        return f"{value / 1e6:.2f}M ISK"
    if value >= 1e3:
        return f"{value / 1e3:.1f}k ISK"
    return f"{value:.0f} ISK"


def _print_stats(stats: FitStats) -> None:
    cap_str = (
        f"Stable at {stats.cap_stable_pct:.1f}%"
        if stats.cap_stable and stats.cap_stable_pct is not None
        else f"Lasts {_format_duration(stats.cap_duration_s or 0.0)}"
    )
    print(f"[{stats.ship}, {stats.fit_name}]")
    print(f"  DPS:         {stats.dps_total:.1f} (weapon {stats.dps_weapon:.1f}, drone {stats.dps_drone:.1f})")
    print(f"  Volley:      {stats.volley_total:.1f}")
    print(
        f"  EHP:         {stats.ehp_total:.0f} "
        f"(S {stats.ehp_shield:.0f} / A {stats.ehp_armor:.0f} / H {stats.ehp_hull:.0f})"
    )
    print(f"  CPU:         {stats.cpu_used:.2f} / {stats.cpu_output:.2f} tf")
    print(f"  Powergrid:   {stats.pg_used:.2f} / {stats.pg_output:.2f} MW")
    print(f"  Calibration: {stats.calibration_used:.0f} / {stats.calibration_output:.0f}")
    print(f"  Speed:       {stats.max_speed:.1f} m/s  |  Align: {stats.align_time:.2f} s")
    print(f"  Capacitor:   {cap_str} (delta {stats.cap_delta:+.2f} GJ/s)")
    print(f"  Versions:    Pyfa {stats.pyfa_version} | Gamedata {stats.gamedata_version}")


def cmd_calc(args: argparse.Namespace) -> int:
    fit_path = Path(args.fit_file)
    if not fit_path.is_file():
        print(f"Error: fit file not found: {fit_path}", file=sys.stderr)
        return 1

    text = fit_path.read_text(encoding="utf-8")
    fit_spec = eft.parse_eft(text)
    skillset = SkillSet(preset=args.skills)
    stats = engine.calculate(fit_spec, skillset)

    if args.json:
        print(json.dumps(stats.model_dump(), indent=2))
    else:
        _print_stats(stats)
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    fit_path = Path(args.fit_file)
    if not fit_path.is_file():
        print(f"Error: fit file not found: {fit_path}", file=sys.stderr)
        return 1

    text = fit_path.read_text(encoding="utf-8")
    fit_spec = eft.parse_eft(text)
    skillset = SkillSet(preset=args.skills)
    profile = profiles.get_profile(args.profile)
    price_provider = pricing.FuzzworkPriceProvider(offline=args.offline_prices)

    baseline, cands = variants.generate_candidates(
        fit_spec=fit_spec,
        profile=profile,
        price_provider=price_provider,
        skills=skillset,
        max_evaluations=args.max_evals,
        top_n=args.top,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "profile": profile.model_dump(),
                    "baseline": baseline.model_dump(),
                    "candidates": [c.model_dump() for c in cands],
                },
                indent=2,
            )
        )
        return 0

    print(f"Profile: {profile.name} ({profile.key})")
    print(
        f"Baseline: DPS {baseline.stats.dps_total:.1f} | EHP {baseline.stats.ehp_total:.0f} | "
        f"Speed {baseline.stats.max_speed:.1f} m/s | Cost {_format_isk(baseline.total_cost_isk)} | "
        f"Score {baseline.score:.3f}"
    )
    if not cands:
        print("No legal candidates found within profile constraints.")
        return 0

    for idx, cand in enumerate(cands, start=1):
        print(f"\n--- Candidate #{idx} (score {cand.score:.3f}, cost {_format_isk(cand.total_cost_isk)}) ---")
        print("Changes:", "; ".join(cand.changes))
        print(
            f"  DPS: {cand.stats.dps_total:.1f} ({cand.stats.dps_total - baseline.stats.dps_total:+.1f}) | "
            f"EHP: {cand.stats.ehp_total:.0f} ({cand.stats.ehp_total - baseline.stats.ehp_total:+.0f}) | "
            f"Speed: {cand.stats.max_speed:.1f} m/s"
        )
    return 0


def cmd_esi_login(args: argparse.Namespace) -> int:
    client_id = (os.environ.get("ESI_CLIENT_ID") or "").strip()
    if not client_id:
        print("Error: ESI_CLIENT_ID is not set in .env or environment.", file=sys.stderr)
        return 1

    port = args.port if args.port is not None else int(os.environ.get("ESI_CALLBACK_PORT") or "8765")
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    verifier, challenge = skills.generate_pkce_pair()
    state = secrets.token_urlsafe(24)

    auth_url = skills.build_authorize_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        state=state,
    )
    print("Open the following URL in your browser to authenticate with EVE SSO:")
    print(auth_url)
    print(f"Waiting for callback on {redirect_uri} ...")

    code = skills.run_pkce_callback_server(expected_state=state, port=port)
    client = skills.ESIClient(client_id=client_id)
    token = client.exchange_code(code, verifier)
    skillset = client.fetch_skills(token.character_id)
    print(f"Authenticated character: {skillset.character_name} ({len(skillset.skills)} skills imported)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="advisor.cli", description="EVE Fitting Advisor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    calc_p = sub.add_parser("calc", help="Calculate stats for an EFT fit file")
    calc_p.add_argument("fit_file", help="Path to an EFT .eft fit file")
    calc_p.add_argument(
        "--skills",
        choices=["all5", "all0"],
        default="all5",
        help="Skill preset (default: all5)",
    )
    calc_p.add_argument("--json", action="store_true", help="Output stats as JSON")
    calc_p.set_defaults(func=cmd_calc)

    sugg_p = sub.add_parser("suggest", help="Suggest alternative fits for a profile")
    sugg_p.add_argument("fit_file", help="Path to an EFT .eft fit file")
    sugg_p.add_argument("--profile", default="pve", help="Profile key (bling, cheap_fw, pve, budget_pvp)")
    sugg_p.add_argument("--skills", choices=["all5", "all0"], default="all5")
    sugg_p.add_argument("--max-evals", type=int, default=40, help="Max candidate evaluations")
    sugg_p.add_argument("--top", type=int, default=5, help="Number of candidates to show")
    sugg_p.add_argument("--offline-prices", action="store_true", help="Do not hit Fuzzwork API")
    sugg_p.add_argument("--json", action="store_true", help="Output JSON")
    sugg_p.set_defaults(func=cmd_suggest)

    esi_p = sub.add_parser("esi-login", help="Authenticate via EVE SSO PKCE and import skills")
    esi_p.add_argument("--port", type=int, default=None, help="Localhost callback port (default: 8765)")
    esi_p.set_defaults(func=cmd_esi_login)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
