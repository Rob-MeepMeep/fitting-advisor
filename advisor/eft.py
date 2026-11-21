"""EFT (EVE Fitting Tool) parser and exporter.

Determines each module's slot from gamedata (via ``advisor.engine.get_item_info``)
and uses section order only as a fallback. Performs exact item name matching only
(never fuzzy-matches or silently substitutes).
"""

from __future__ import annotations

import re

from advisor import engine
from advisor.models import CargoSpec, DroneSpec, FitSpec, ModuleSpec, SlotType

_HEADER_RE = re.compile(r"^\[([^,\]]+),\s*(.+?)\]\s*$")
_EMPTY_SLOT_RE = re.compile(r"^\[Empty\s+(Low|Med|High|Rig|Subsystem)\s+slot\]$", re.IGNORECASE)
_QTY_RE = re.compile(r"^(.+?)\s+x(\d+)$")

_EMPTY_SLOT_MAP: dict[str, SlotType] = {
    "low": "low",
    "med": "med",
    "high": "high",
    "rig": "rig",
    "subsystem": "subsystem",
}

_SECTION_FALLBACK_ORDER: tuple[SlotType, ...] = ("low", "med", "high", "rig", "subsystem")

_EMPTY_LABEL: dict[SlotType, str] = {
    "low": "[Empty Low slot]",
    "med": "[Empty Med slot]",
    "high": "[Empty High slot]",
    "rig": "[Empty Rig slot]",
    "subsystem": "[Empty Subsystem slot]",
}


class EFTParseError(ValueError):
    """Raised when an EFT string cannot be parsed or contains an unknown item."""

    def __init__(self, message: str, line_number: int | None = None, suggestions: list[str] | None = None):
        super().__init__(message)
        self.line_number = line_number
        self.suggestions = suggestions or []


def parse_eft(text: str) -> FitSpec:
    """Parse an EFT-formatted fit string into a ``FitSpec``."""
    raw_lines = text.splitlines()

    # Find header line (first non-blank line)
    header_idx: int | None = None
    ship_name = ""
    fit_name = ""
    for idx, raw in enumerate(raw_lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        match = _HEADER_RE.match(stripped)
        if not match:
            raise EFTParseError(
                f"Line {idx}: Expected EFT header '[Ship Type, Fit Name]', got {stripped!r}",
                line_number=idx,
            )
        ship_name = match.group(1).strip()
        fit_name = match.group(2).strip()
        header_idx = idx
        break

    if header_idx is None:
        raise EFTParseError("Empty EFT input", line_number=1)

    ship_info = engine.get_item_info(ship_name)
    if ship_info is None:
        suggestions = engine.suggest_item_names(ship_name)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise EFTParseError(
            f"Line {header_idx}: Unknown ship type {ship_name!r}.{hint}",
            line_number=header_idx,
            suggestions=suggestions,
        )

    lows: list[ModuleSpec] = []
    mids: list[ModuleSpec] = []
    highs: list[ModuleSpec] = []
    rigs: list[ModuleSpec] = []
    subsystems: list[ModuleSpec] = []
    drones: list[DroneSpec] = []
    fighters: list[DroneSpec] = []
    cargo: list[CargoSpec] = []

    buckets: dict[SlotType, list[ModuleSpec]] = {
        "low": lows,
        "med": mids,
        "high": highs,
        "rig": rigs,
        "subsystem": subsystems,
    }

    section_idx = 0
    in_section = False

    for line_no, raw in enumerate(raw_lines[header_idx:], start=header_idx + 1):
        line = raw.strip()
        if not line:
            if in_section:
                section_idx += 1
                in_section = False
            continue
        in_section = True

        # Empty slot marker
        empty_match = _EMPTY_SLOT_RE.match(line)
        if empty_match:
            slot = _EMPTY_SLOT_MAP[empty_match.group(1).lower()]
            buckets[slot].append(ModuleSpec(empty=True, slot=slot))
            continue

        # Quantity suffix: drones, fighters, or cargo (e.g. "Hobgoblin II x5")
        qty_match = _QTY_RE.match(line)
        if qty_match:
            item_name = qty_match.group(1).strip()
            qty = int(qty_match.group(2))
            if qty <= 0:
                raise EFTParseError(
                    f"Line {line_no}: Quantity must be positive, got {qty}",
                    line_number=line_no,
                )
            info = engine.get_item_info(item_name)
            if info is None:
                suggestions = engine.suggest_item_names(item_name)
                hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
                raise EFTParseError(
                    f"Line {line_no}: Unknown item {item_name!r}.{hint}",
                    line_number=line_no,
                    suggestions=suggestions,
                )
            if info.category_name == "Drone":
                drones.append(DroneSpec(name=info.name, quantity=qty))
            elif info.category_name == "Fighter":
                fighters.append(DroneSpec(name=info.name, quantity=qty))
            else:
                cargo.append(CargoSpec(name=info.name, quantity=qty))
            continue

        # Offline suffix
        state: str | None = None
        if line.endswith(("/OFFLINE", "/offline")):
            state = "offline"
            line = line[: -len("/OFFLINE")].strip()

        # Optional charge after comma
        charge_name: str | None = None
        if "," in line:
            mod_part, charge_part = line.split(",", 1)
            mod_name = mod_part.strip()
            charge_name = charge_part.strip() or None
        else:
            mod_name = line

        mod_info = engine.get_item_info(mod_name)
        if mod_info is None:
            suggestions = engine.suggest_item_names(mod_name)
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            raise EFTParseError(
                f"Line {line_no}: Unknown module {mod_name!r}.{hint}",
                line_number=line_no,
                suggestions=suggestions,
            )

        if charge_name is not None:
            charge_info = engine.get_item_info(charge_name)
            if charge_info is None:
                suggestions = engine.suggest_item_names(charge_name)
                hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
                raise EFTParseError(
                    f"Line {line_no}: Unknown charge {charge_name!r}.{hint}",
                    line_number=line_no,
                    suggestions=suggestions,
                )
            charge_name = charge_info.name

        # Determine slot from gamedata first, falling back to section order
        slot: SlotType | None = mod_info.slot if mod_info.slot in buckets else None
        if slot is None:
            if section_idx < len(_SECTION_FALLBACK_ORDER):
                slot = _SECTION_FALLBACK_ORDER[section_idx]
            else:
                # Non-module item without quantity suffix in trailing section -> treat as cargo x1
                cargo.append(CargoSpec(name=mod_info.name, quantity=1))
                continue

        buckets[slot].append(
            ModuleSpec(
                name=mod_info.name,
                charge=charge_name,
                state=state,  # type: ignore[arg-type]
                slot=slot,
                empty=False,
            )
        )

    return FitSpec(
        ship=ship_info.name,
        name=fit_name,
        lows=lows,
        mids=mids,
        highs=highs,
        rigs=rigs,
        subsystems=subsystems,
        drones=drones,
        fighters=fighters,
        cargo=cargo,
    )


def _format_module(slot: SlotType, mod: ModuleSpec) -> str:
    if mod.empty:
        return _EMPTY_LABEL[slot]
    text = f"{mod.name}, {mod.charge}" if mod.charge else mod.name
    if mod.state == "offline":
        text += " /OFFLINE"
    return text


def export_eft(fit: FitSpec) -> str:
    """Export a ``FitSpec`` to canonical EFT format."""
    sections: list[list[str]] = []

    for slot_name, bucket in (
        ("low", fit.lows),
        ("med", fit.mids),
        ("high", fit.highs),
        ("rig", fit.rigs),
        ("subsystem", fit.subsystems),
    ):
        if bucket:
            sections.append([_format_module(slot_name, m) for m in bucket])

    if fit.drones or fit.fighters:
        drone_lines = [f"{d.name} x{d.quantity}" for d in fit.drones]
        drone_lines.extend(f"{f.name} x{f.quantity}" for f in fit.fighters)
        sections.append(drone_lines)

    if fit.cargo:
        sections.append([f"{c.name} x{c.quantity}" for c in fit.cargo])

    lines = [f"[{fit.ship}, {fit.name}]", ""]
    for idx, sec in enumerate(sections):
        lines.extend(sec)
        if idx < len(sections) - 1:
            lines.append("")
    return "\n".join(lines) + "\n"
