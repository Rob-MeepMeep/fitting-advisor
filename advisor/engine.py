"""Headless wrapper around Pyfa's eos calculation engine.

This is the ONLY module in fitting-advisor allowed to import ``eos`` or Pyfa's
``config``. Everything else interacts through plain Pydantic models from
``advisor.models``.
"""

from __future__ import annotations

import difflib
import sys
import threading
from pathlib import Path
from typing import Any

from advisor import wxstub
from advisor.models import (
    FitSpec,
    FitStats,
    FittedModuleInfo,
    ItemInfo,
    ModuleSpec,
    ResistProfile,
    SkillSet,
    SlotType,
    ValidationIssue,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PYFA_ROOT = REPO_ROOT / "vendor" / "pyfa"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "eve.db"

_ENGINE_LOCK = threading.RLock()
_INITIALIZED = False
_PYFA_VERSION = "unknown"
_GAMEDATA_VERSION = "unknown"

# Lazy-populated eos references after init_engine()
_eos_db: Any = None
_Fit: Any = None
_Ship: Any = None
_Module: Any = None
_Drone: Any = None
_Fighter: Any = None
_Cargo: Any = None
_Character: Any = None
_Skill: Any = None
_FittingModuleState: Any = None
_FittingSlot: Any = None
_FittingHardpoint: Any = None
_Item: Any = None

_SLOT_TO_EOS: dict[SlotType, Any] = {}
_EOS_TO_SLOT: dict[Any, SlotType] = {}

_ALL_ITEM_NAMES_CACHE: list[str] | None = None


def init_engine(db_path: Path | None = None) -> None:
    """Bootstrap Pyfa's eos engine headlessly (idempotent)."""
    global _INITIALIZED, _PYFA_VERSION, _GAMEDATA_VERSION
    global _eos_db, _Fit, _Ship, _Module, _Drone, _Fighter, _Cargo
    global _Character, _Skill, _FittingModuleState, _FittingSlot, _FittingHardpoint, _Item

    with _ENGINE_LOCK:
        if _INITIALIZED:
            return

        eve_db = (db_path or DEFAULT_DB_PATH).resolve()
        if not eve_db.is_file():
            raise FileNotFoundError(
                f"Pyfa gamedata database not found at {eve_db}. "
                "Run `uv run python scripts/build_db.py` first."
            )

        if str(PYFA_ROOT) not in sys.path:
            sys.path.insert(0, str(PYFA_ROOT))

        # Must be installed before importing anything from Pyfa.
        wxstub.install()

        import eos.config  # type: ignore[import-not-found]

        import config  # type: ignore[import-not-found]

        # Keep Pyfa's internal state directory inside workspace data/ rather than ~/.pyfa
        pyfa_state_dir = REPO_ROOT / "data" / ".pyfa_state"
        pyfa_state_dir.mkdir(parents=True, exist_ok=True)
        config.defPaths(str(pyfa_state_dir))

        gamedata_conn = f"sqlite:///{eve_db.as_posix()}?check_same_thread=False"
        # Use a named shared-cache in-memory SQLite URI so FastAPI worker threads
        # share the same in-memory tables safely under _ENGINE_LOCK.
        saveddata_conn = (
            "sqlite:///file:pyfa_saveddata?mode=memory&cache=shared&uri=true&check_same_thread=False"
        )

        config.gamedata_connectionstring = gamedata_conn
        config.saveddata_connectionstring = saveddata_conn
        eos.config.gamedata_connectionstring = gamedata_conn
        eos.config.saveddata_connectionstring = saveddata_conn

        import eos.db  # type: ignore[import-not-found]
        from eos.const import (  # type: ignore[import-not-found]
            FittingHardpoint,
            FittingModuleState,
            FittingSlot,
        )
        from eos.gamedata import Item  # type: ignore[import-not-found]
        from eos.saveddata.cargo import Cargo  # type: ignore[import-not-found]
        from eos.saveddata.character import Character, Skill  # type: ignore[import-not-found]
        from eos.saveddata.drone import Drone  # type: ignore[import-not-found]
        from eos.saveddata.fighter import Fighter  # type: ignore[import-not-found]
        from eos.saveddata.fit import Fit  # type: ignore[import-not-found]
        from eos.saveddata.module import Module  # type: ignore[import-not-found]
        from eos.saveddata.ship import Ship  # type: ignore[import-not-found]

        # SQLAlchemy 2.x: bind is REQUIRED.
        eos.db.saveddata_meta.create_all(eos.db.saveddata_engine)

        # Pre-warm All 0 and All 5 characters before any transient Fit exists
        # to avoid cross-thread writes or SQLAlchemy relationship warnings later.
        Character.getAll0()
        Character.getAll5()

        _eos_db = eos.db
        _Fit = Fit
        _Ship = Ship
        _Module = Module
        _Drone = Drone
        _Fighter = Fighter
        _Cargo = Cargo
        _Character = Character
        _Skill = Skill
        _FittingModuleState = FittingModuleState
        _FittingSlot = FittingSlot
        _FittingHardpoint = FittingHardpoint
        _Item = Item

        _SLOT_TO_EOS.update(
            {
                "low": FittingSlot.LOW,
                "med": FittingSlot.MED,
                "high": FittingSlot.HIGH,
                "rig": FittingSlot.RIG,
                "subsystem": FittingSlot.SUBSYSTEM,
                "service": FittingSlot.SERVICE,
            }
        )
        _EOS_TO_SLOT.update({v: k for k, v in _SLOT_TO_EOS.items()})
        _EOS_TO_SLOT.update({v.value: k for k, v in _SLOT_TO_EOS.items()})

        _PYFA_VERSION = str(config.getVersion() or "unknown")
        _GAMEDATA_VERSION = str(eos.config.gamedata_version or "unknown")
        _INITIALIZED = True


def get_versions() -> dict[str, str]:
    """Return Pyfa version and game data build version."""
    init_engine()
    return {
        "pyfa_version": _PYFA_VERSION,
        "gamedata_version": _GAMEDATA_VERSION,
    }


def _item_to_info(item: Any) -> ItemInfo:
    slot_enum = _Module.calculateSlot(item)
    slot_name = _EOS_TO_SLOT.get(slot_enum)
    meta_group = item.metaGroup.name if getattr(item, "metaGroup", None) is not None else "Tech I"

    def _attr(name: str) -> float | None:
        a = item.attributes.get(name)
        return float(a.value) if a is not None and a.value is not None else None

    meta_level_val = _attr("metaLevel")
    rig_size_val = _attr("rigSize")
    charge_size_val = _attr("chargeSize")

    return ItemInfo(
        type_id=int(item.ID),
        name=str(item.name),
        group_id=int(item.group.ID) if item.group else 0,
        group_name=str(item.group.name) if item.group else "",
        category_name=str(item.category.name) if item.category else "",
        slot=slot_name,
        published=bool(item.published),
        meta_group=str(meta_group),
        meta_level=int(meta_level_val) if meta_level_val is not None else None,
        volume=_attr("volume"),
        rig_size=int(rig_size_val) if rig_size_val is not None else None,
        charge_size=int(charge_size_val) if charge_size_val is not None else None,
    )


def get_item_info(name: str) -> ItemInfo | None:
    """Exact item lookup by English name via ``eos.db.getItem``."""
    init_engine()
    with _ENGINE_LOCK:
        item = _eos_db.getItem(name)
        if item is None:
            return None
        return _item_to_info(item)


def get_item_info_by_id(type_id: int) -> ItemInfo | None:
    """Exact item lookup by EVE type ID."""
    init_engine()
    with _ENGINE_LOCK:
        item = _eos_db.getItem(int(type_id))
        if item is None:
            return None
        return _item_to_info(item)


def suggest_item_names(name: str, limit: int = 5) -> list[str]:
    """Return close published item name suggestions (for error messages only, never auto-applied)."""
    global _ALL_ITEM_NAMES_CACHE
    init_engine()
    with _ENGINE_LOCK:
        if _ALL_ITEM_NAMES_CACHE is None:
            rows = (
                _eos_db.gamedata_session.query(_Item.typeName)
                .filter(_Item.published.is_(True))
                .all()
            )
            _ALL_ITEM_NAMES_CACHE = [r[0] for r in rows if r[0]]
        return difflib.get_close_matches(name, _ALL_ITEM_NAMES_CACHE, n=limit, cutoff=0.6)


def _resolve_character(skills: SkillSet | None) -> Any:
    if skills is None or skills.preset == "all5":
        return _Character.getAll5()
    if skills.preset == "all0":
        return _Character.getAll0()
    # Custom character from ESI skill map (type_id -> level)
    char = _Character(skills.character_name or "Custom Character", defaultLevel=0, initSkills=False)
    skill_id_map = _Character.getSkillIDMap()
    skill_levels = {int(k): max(0, min(5, int(v))) for k, v in skills.skills.items()}
    for tid in skill_id_map:
        char.addSkill(_Skill(char, tid, skill_levels.get(tid, 0)))
    return char


def _resolve_module_state(mod: Any, requested_state: str | None) -> Any:
    if requested_state == "offline":
        return _FittingModuleState.OFFLINE
    if requested_state == "online":
        return _FittingModuleState.ONLINE
    if requested_state == "overheated" and mod.isValidState(_FittingModuleState.OVERHEATED):
        return _FittingModuleState.OVERHEATED
    if mod.isValidState(_FittingModuleState.ACTIVE):
        return _FittingModuleState.ACTIVE
    return _FittingModuleState.ONLINE


def _state_to_str(state: Any) -> str:
    if state == _FittingModuleState.OFFLINE:
        return "offline"
    if state == _FittingModuleState.ONLINE:
        return "online"
    if state == _FittingModuleState.ACTIVE:
        return "active"
    if state == _FittingModuleState.OVERHEATED:
        return "overheated"
    return "online"


def _build_eos_fit(
    fit_spec: FitSpec,
    skills: SkillSet | None = None,
    activate_drones: bool = True,
) -> tuple[Any, list[tuple[SlotType, ModuleSpec, Any]]]:
    """Construct an eos ``Fit`` from a ``FitSpec`` and run attribute calculation."""
    ship_item = _eos_db.getItem(fit_spec.ship)
    if ship_item is None:
        raise ValueError(f"Unknown ship type: {fit_spec.ship!r}")
    try:
        ship = _Ship(ship_item)
    except ValueError as exc:
        raise ValueError(f"Item is not a valid ship: {fit_spec.ship!r}") from exc

    fit = _Fit(ship=ship, name=fit_spec.name)
    fit.character = _resolve_character(skills)

    built_modules: list[tuple[SlotType, ModuleSpec, Any]] = []

    for slot_name, mod_spec in fit_spec.iter_modules():
        if mod_spec.empty:
            built_modules.append((slot_name, mod_spec, None))
            continue

        item = _eos_db.getItem(mod_spec.name)
        if item is None:
            raise ValueError(f"Unknown module: {mod_spec.name!r}")

        mod = _Module(item)
        mod.owner = fit  # Required by eos (see AGENTS.md §5)

        if mod_spec.charge:
            charge_item = _eos_db.getItem(mod_spec.charge)
            if charge_item is None:
                raise ValueError(f"Unknown charge: {mod_spec.charge!r}")
            mod.charge = charge_item

        mod.state = _resolve_module_state(mod, mod_spec.state)
        fit.modules.append(mod)
        built_modules.append((slot_name, mod_spec, mod))

    # Add drones (activate up to bandwidth & max active drones unless explicitly specified)
    for drone_spec in fit_spec.drones:
        drone_item = _eos_db.getItem(drone_spec.name)
        if drone_item is None:
            raise ValueError(f"Unknown drone: {drone_spec.name!r}")
        drone = _Drone(drone_item)
        drone.owner = fit
        drone.amount = drone_spec.quantity
        drone.amountActive = 0
        fit.drones.append(drone)

    # Add fighters
    for fighter_spec in fit_spec.fighters:
        fighter_item = _eos_db.getItem(fighter_spec.name)
        if fighter_item is None:
            raise ValueError(f"Unknown fighter: {fighter_spec.name!r}")
        fighter = _Fighter(fighter_item)
        fighter.owner = fit
        fighter.amount = fighter_spec.quantity
        fighter.active = fighter_spec.active is None or fighter_spec.active > 0
        fit.fighters.append(fighter)

    # Add cargo
    for cargo_spec in fit_spec.cargo:
        cargo_item = _eos_db.getItem(cargo_spec.name)
        if cargo_item is None:
            raise ValueError(f"Unknown cargo item: {cargo_spec.name!r}")
        cargo = _Cargo(cargo_item)
        cargo.owner = fit
        cargo.amount = cargo_spec.quantity
        fit.cargo.append(cargo)

    # First pass to get modified ship attributes (subsystems, skills, drone bandwidth, max active drones)
    fit.calculateModifiedAttributes()

    # Activate drones intelligently if any are present
    if fit.drones and (activate_drones or any(d.active is not None for d in fit_spec.drones)):
        max_active_drones = int(fit.extraAttributes["maxActiveDrones"] or 0)
        bw_total = float(fit.ship.getModifiedItemAttr("droneBandwidth") or 0.0)
        active_used = 0
        bw_used = 0.0
        for drone, drone_spec in zip(fit.drones, fit_spec.drones):
            bw_each = float(drone.getModifiedItemAttr("droneBandwidthUsed") or 0.0)
            if drone_spec.active is not None:
                count = min(drone_spec.active, drone_spec.quantity)
            elif not activate_drones:
                count = 0
            else:
                remaining_slots = max(0, max_active_drones - active_used)
                if bw_each > 0:
                    remaining_bw = int((bw_total - bw_used) // bw_each)
                else:
                    remaining_bw = drone_spec.quantity
                count = max(0, min(drone_spec.quantity, remaining_slots, remaining_bw))
            drone.amountActive = count
            active_used += count
            bw_used += count * bw_each
        # Recalculate with active drones
        fit.clear()
        fit.calculateModifiedAttributes()

    return fit, built_modules


def _extract_resists(ship: Any, prefix: str) -> ResistProfile:
    def _res(dmg: str) -> float:
        attr = f"{prefix}{dmg}DamageResonance" if prefix else f"{dmg.lower()}DamageResonance"
        val = ship.getModifiedItemAttr(attr)
        if val is None:
            return 0.0
        return round((1.0 - float(val)) * 100.0, 2)

    return ResistProfile(
        em=_res("Em"),
        thermal=_res("Thermal"),
        kinetic=_res("Kinetic"),
        explosive=_res("Explosive"),
    )


def calculate(
    fit_spec: FitSpec,
    skills: SkillSet | None = None,
    activate_drones: bool = True,
) -> FitStats:
    """Calculate all ship fit statistics using Pyfa's eos engine."""
    init_engine()
    with _ENGINE_LOCK:
        fit, built_modules = _build_eos_fit(fit_spec, skills, activate_drones=activate_drones)

        # Explicitly run capacitor simulation after calculateModifiedAttributes()
        fit.simulateCap()
        cap_stable = bool(fit.capStable)
        cap_state = float(fit.capState)

        total_dps = fit.getTotalDps()
        weapon_dps = fit.getWeaponDps()
        drone_dps = fit.getDroneDps()
        total_volley = fit.getTotalVolley()
        weapon_volley = fit.getWeaponVolley()
        drone_volley = fit.getDroneVolley()

        ehp = fit.ehp
        hp = fit.hp
        eff_tank = fit.effectiveSustainableTank

        slots_total: dict[str, int] = {}
        slots_used: dict[str, int] = {}
        for slot_name in ("high", "med", "low", "rig", "subsystem"):
            slot_enum = _SLOT_TO_EOS[slot_name]
            slots_total[slot_name] = int(fit.getNumSlots(slot_enum) or 0)
            slots_used[slot_name] = int(fit.getSlotsUsed(slot_enum.value) or 0)

        resolved_modules: list[FittedModuleInfo] = []
        for slot_name, mod_spec, mod in built_modules:
            if mod is None:
                resolved_modules.append(
                    FittedModuleInfo(slot=slot_name, name="", state="offline", empty=True)
                )
            else:
                meta_group = (
                    mod.item.metaGroup.name
                    if getattr(mod.item, "metaGroup", None) is not None
                    else "Tech I"
                )
                resolved_modules.append(
                    FittedModuleInfo(
                        slot=slot_name,
                        name=str(mod.item.name),
                        type_id=int(mod.item.ID),
                        charge=str(mod.charge.name) if mod.charge else None,
                        charge_type_id=int(mod.charge.ID) if mod.charge else None,
                        state=_state_to_str(mod.state),
                        meta_group=str(meta_group),
                        empty=False,
                    )
                )

        return FitStats(
            ship=str(fit.ship.item.name),
            ship_type_id=int(fit.ship.item.ID),
            fit_name=str(fit.name),
            dps_total=float(total_dps.total),
            dps_weapon=float(weapon_dps.total),
            dps_drone=float(drone_dps.total),
            volley_total=float(total_volley.total),
            volley_weapon=float(weapon_volley.total),
            volley_drone=float(drone_volley.total),
            ehp_total=float(ehp["shield"] + ehp["armor"] + ehp["hull"]),
            ehp_shield=float(ehp["shield"]),
            ehp_armor=float(ehp["armor"]),
            ehp_hull=float(ehp["hull"]),
            hp_shield=float(hp["shield"]),
            hp_armor=float(hp["armor"]),
            hp_hull=float(hp["hull"]),
            resists_shield=_extract_resists(fit.ship, "shield"),
            resists_armor=_extract_resists(fit.ship, "armor"),
            resists_hull=_extract_resists(fit.ship, ""),
            tank_passive_shield=float(eff_tank.get("passiveShield") or 0.0),
            tank_shield_repair=float(eff_tank.get("shieldRepair") or 0.0),
            tank_armor_repair=float(eff_tank.get("armorRepair") or 0.0),
            tank_hull_repair=float(eff_tank.get("hullRepair") or 0.0),
            cpu_used=float(fit.cpuUsed),
            cpu_output=float(fit.ship.getModifiedItemAttr("cpuOutput") or 0.0),
            pg_used=float(fit.pgUsed),
            pg_output=float(fit.ship.getModifiedItemAttr("powerOutput") or 0.0),
            calibration_used=float(fit.calibrationUsed),
            calibration_output=float(fit.ship.getModifiedItemAttr("upgradeCapacity") or 0.0),
            max_speed=float(fit.maxSpeed or 0.0),
            align_time=float(fit.alignTime or 0.0),
            warp_speed=float(fit.warpSpeed or 0.0),
            signature_radius=float(fit.ship.getModifiedItemAttr("signatureRadius") or 0.0),
            mass=float(fit.ship.getModifiedItemAttr("mass") or 0.0),
            cap_capacity=float(fit.ship.getModifiedItemAttr("capacitorCapacity") or 0.0),
            cap_stable=cap_stable,
            cap_stable_pct=cap_state if cap_stable else None,
            cap_duration_s=None if cap_stable else cap_state,
            cap_used=float(fit.capUsed or 0.0),
            cap_recharge=float(fit.capRecharge or 0.0),
            cap_delta=float(fit.capDelta or 0.0),
            drone_bandwidth_used=float(fit.droneBandwidthUsed or 0.0),
            drone_bandwidth_total=float(fit.ship.getModifiedItemAttr("droneBandwidth") or 0.0),
            drone_bay_used=float(fit.droneBayUsed or 0.0),
            drone_bay_total=float(fit.ship.getModifiedItemAttr("droneCapacity") or 0.0),
            slots_total=slots_total,
            slots_used=slots_used,
            turrets_used=int(fit.getHardpointsUsed(_FittingHardpoint.TURRET)),
            turrets_total=int(fit.ship.getModifiedItemAttr("turretSlotsLeft") or 0),
            launchers_used=int(fit.getHardpointsUsed(_FittingHardpoint.MISSILE)),
            launchers_total=int(fit.ship.getModifiedItemAttr("launcherSlotsLeft") or 0),
            modules=resolved_modules,
            pyfa_version=_PYFA_VERSION,
            gamedata_version=_GAMEDATA_VERSION,
        )


def validate_fit_eos(fit_spec: FitSpec, skills: SkillSet | None = None) -> list[ValidationIssue]:
    """Validate a fit against EVE fitting rules using eos attributes and helpers.

    Returns ALL violations found, never stopping at the first.
    """
    init_engine()
    issues: list[ValidationIssue] = []

    with _ENGINE_LOCK:
        ship_item = _eos_db.getItem(fit_spec.ship)
        if ship_item is None:
            return [ValidationIssue(code="unknown_ship", message=f"Unknown ship type: '{fit_spec.ship}'")]
        try:
            ship = _Ship(ship_item)
        except ValueError:
            return [ValidationIssue(code="invalid_ship", message=f"'{fit_spec.ship}' is not a valid ship")]

        fit = _Fit(ship=ship, name=fit_spec.name)
        fit.character = _resolve_character(skills)

        # Build modules, collecting unknown-item errors instead of aborting
        valid_mods: list[tuple[SlotType, ModuleSpec, Any]] = []
        for slot_name, mod_spec in fit_spec.iter_modules():
            if mod_spec.empty:
                continue
            item = _eos_db.getItem(mod_spec.name)
            if item is None:
                issues.append(
                    ValidationIssue(
                        code="unknown_module",
                        message=f"Unknown module: '{mod_spec.name}'",
                        module_name=mod_spec.name,
                    )
                )
                continue
            try:
                mod = _Module(item)
            except ValueError:
                issues.append(
                    ValidationIssue(
                        code="not_a_module",
                        message=f"'{mod_spec.name}' is not a fittable module",
                        module_name=mod_spec.name,
                    )
                )
                continue

            mod.owner = fit
            if mod_spec.charge:
                charge_item = _eos_db.getItem(mod_spec.charge)
                if charge_item is None:
                    issues.append(
                        ValidationIssue(
                            code="unknown_charge",
                            message=f"Unknown charge '{mod_spec.charge}' for '{mod_spec.name}'",
                            module_name=mod_spec.name,
                        )
                    )
                else:
                    mod.charge = charge_item

            mod.state = _resolve_module_state(mod, mod_spec.state)
            fit.modules.append(mod)
            valid_mods.append((slot_name, mod_spec, mod))

        for drone_spec in fit_spec.drones:
            drone_item = _eos_db.getItem(drone_spec.name)
            if drone_item is None:
                issues.append(
                    ValidationIssue(
                        code="unknown_drone",
                        message=f"Unknown drone: '{drone_spec.name}'",
                        module_name=drone_spec.name,
                    )
                )
                continue
            try:
                drone = _Drone(drone_item)
                drone.owner = fit
                drone.amount = drone_spec.quantity
                if drone_spec.active is not None:
                    if drone_spec.active < 0:
                        issues.append(
                            ValidationIssue(
                                code="invalid_active_drones",
                                message=(
                                    f"Active count for '{drone_spec.name}' cannot be negative "
                                    f"({drone_spec.active})"
                                ),
                                module_name=drone_spec.name,
                            )
                        )
                        drone.amountActive = 0
                    else:
                        if drone_spec.active > drone_spec.quantity:
                            issues.append(
                                ValidationIssue(
                                    code="active_drones_exceed_quantity",
                                    message=(
                                        f"Active count for '{drone_spec.name}' ({drone_spec.active}) "
                                        f"exceeds carried quantity ({drone_spec.quantity})"
                                    ),
                                    module_name=drone_spec.name,
                                )
                            )
                        drone.amountActive = drone_spec.active
                else:
                    drone.amountActive = 0
                fit.drones.append(drone)
            except ValueError:
                issues.append(
                    ValidationIssue(
                        code="not_a_drone",
                        message=f"'{drone_spec.name}' is not a valid drone",
                        module_name=drone_spec.name,
                    )
                )

        fit.calculateModifiedAttributes()

        eps = 1e-4

        # 1. CPU, Powergrid, Calibration
        cpu_used = float(fit.cpuUsed)
        cpu_out = float(fit.ship.getModifiedItemAttr("cpuOutput") or 0.0)
        if cpu_used > cpu_out + eps:
            issues.append(
                ValidationIssue(
                    code="cpu_exceeded",
                    message=f"CPU exceeded: {cpu_used:.2f} / {cpu_out:.2f} tf",
                )
            )

        pg_used = float(fit.pgUsed)
        pg_out = float(fit.ship.getModifiedItemAttr("powerOutput") or 0.0)
        if pg_used > pg_out + eps:
            issues.append(
                ValidationIssue(
                    code="pg_exceeded",
                    message=f"Powergrid exceeded: {pg_used:.2f} / {pg_out:.2f} MW",
                )
            )

        cal_used = float(fit.calibrationUsed)
        cal_out = float(fit.ship.getModifiedItemAttr("upgradeCapacity") or 0.0)
        if cal_used > cal_out + eps:
            issues.append(
                ValidationIssue(
                    code="calibration_exceeded",
                    message=f"Calibration exceeded: {cal_used:.0f} / {cal_out:.0f}",
                )
            )

        # 2. Slot counts (after subsystems are applied)
        for slot_name in ("low", "med", "high", "rig", "subsystem"):
            slot_enum = _SLOT_TO_EOS[slot_name]
            used = int(fit.getSlotsUsed(slot_enum.value) or 0)
            total = int(fit.getNumSlots(slot_enum) or 0)
            if used > total:
                issues.append(
                    ValidationIssue(
                        code="slots_exceeded",
                        message=f"Too many {slot_name} slot modules: {used} fitted, ship has {total}",
                    )
                )

        # 3. Hardpoints
        turrets_used = int(fit.getHardpointsUsed(_FittingHardpoint.TURRET))
        turrets_total = int(fit.ship.getModifiedItemAttr("turretSlotsLeft") or 0)
        if turrets_used > turrets_total:
            issues.append(
                ValidationIssue(
                    code="turret_hardpoints_exceeded",
                    message=f"Turret hardpoints exceeded: {turrets_used} fitted, ship has {turrets_total}",
                )
            )

        launchers_used = int(fit.getHardpointsUsed(_FittingHardpoint.MISSILE))
        launchers_total = int(fit.ship.getModifiedItemAttr("launcherSlotsLeft") or 0)
        if launchers_used > launchers_total:
            issues.append(
                ValidationIssue(
                    code="launcher_hardpoints_exceeded",
                    message=f"Launcher hardpoints exceeded: {launchers_used} fitted, ship has {launchers_total}",
                )
            )

        # 4. Per-module checks: ship restrictions, capital-on-subcap, rig size,
        #    subsystem duplicate slot, maxGroupFitted, maxGroupActive, maxGroupOnline, charge validity
        ship_rig_size = fit.ship.getModifiedItemAttr("rigSize")
        ship_is_capital = fit.ship.getModifiedItemAttr("isCapitalSize", 0) == 1

        group_fitted_counts: dict[int, int] = {}
        group_online_counts: dict[int, int] = {}
        group_active_counts: dict[int, int] = {}
        group_names: dict[int, str] = {}
        group_max_fitted: dict[int, int] = {}
        group_max_online: dict[int, int] = {}
        group_max_active: dict[int, int] = {}
        seen_sub_slots: dict[int, str] = {}

        for _slot_name, mod_spec, mod in valid_mods:
            item = mod.item
            gid = int(item.groupID)
            group_names[gid] = item.group.name
            group_fitted_counts[gid] = group_fitted_counts.get(gid, 0) + 1
            if mod.state >= _FittingModuleState.ONLINE:
                group_online_counts[gid] = group_online_counts.get(gid, 0) + 1
            if mod.state >= _FittingModuleState.ACTIVE:
                group_active_counts[gid] = group_active_counts.get(gid, 0) + 1

            raw_max_fitted = item.attributes.get("maxGroupFitted")
            if raw_max_fitted is not None and raw_max_fitted.value:
                group_max_fitted[gid] = int(raw_max_fitted.value)
            max_online = mod.getModifiedItemAttr("maxGroupOnline", None)
            if max_online:
                group_max_online[gid] = int(max_online)
            max_active = mod.getModifiedItemAttr("maxGroupActive", None)
            if max_active:
                group_max_active[gid] = int(max_active)

            # Ship-type / ship-group restriction
            if not fit.canFit(item):
                issues.append(
                    ValidationIssue(
                        code="ship_restriction",
                        message=f"'{item.name}' cannot be fitted to '{fit.ship.item.name}'",
                        module_name=item.name,
                    )
                )

            # Capital module on subcapital hull
            if not ship_is_capital and mod.isCapitalSize:
                issues.append(
                    ValidationIssue(
                        code="capital_module_on_subcap",
                        message=f"Capital-sized module '{item.name}' cannot fit on subcapital ship '{fit.ship.item.name}'",
                        module_name=item.name,
                    )
                )

            # Rig size match
            if mod.slot == _FittingSlot.RIG.value:
                mod_rig_size = mod.getModifiedItemAttr("rigSize")
                if mod_rig_size != ship_rig_size:
                    issues.append(
                        ValidationIssue(
                            code="rig_size_mismatch",
                            message=(
                                f"Rig '{item.name}' (size {mod_rig_size}) does not match "
                                f"'{fit.ship.item.name}' rig size ({ship_rig_size})"
                            ),
                            module_name=item.name,
                        )
                    )

            # Duplicate subsystem slot
            if mod.slot == _FittingSlot.SUBSYSTEM.value:
                sub_slot = int(mod.getModifiedItemAttr("subSystemSlot") or 0)
                if sub_slot in seen_sub_slots:
                    issues.append(
                        ValidationIssue(
                            code="duplicate_subsystem_slot",
                            message=(
                                f"Duplicate subsystem slot: '{item.name}' conflicts with "
                                f"'{seen_sub_slots[sub_slot]}'"
                            ),
                            module_name=item.name,
                        )
                    )
                else:
                    seen_sub_slots[sub_slot] = item.name

            # Charge validity
            if mod.charge is not None and not mod.isValidCharge(mod.charge):
                issues.append(
                    ValidationIssue(
                        code="invalid_charge",
                        message=f"Charge '{mod.charge.name}' is not valid for '{item.name}'",
                        module_name=item.name,
                    )
                )

        for gid, limit in group_max_fitted.items():
            if group_fitted_counts.get(gid, 0) > limit:
                issues.append(
                    ValidationIssue(
                        code="max_group_fitted_exceeded",
                        message=(
                            f"Max fitted limit for group '{group_names[gid]}' exceeded: "
                            f"{group_fitted_counts[gid]} fitted, max {limit}"
                        ),
                    )
                )

        for gid, limit in group_max_online.items():
            if group_online_counts.get(gid, 0) > limit:
                issues.append(
                    ValidationIssue(
                        code="max_group_online_exceeded",
                        message=(
                            f"Max online limit for group '{group_names[gid]}' exceeded: "
                            f"{group_online_counts[gid]} online, max {limit}"
                        ),
                    )
                )

        for gid, limit in group_max_active.items():
            if group_active_counts.get(gid, 0) > limit:
                issues.append(
                    ValidationIssue(
                        code="max_group_active_exceeded",
                        message=(
                            f"Max active limit for group '{group_names[gid]}' exceeded: "
                            f"{group_active_counts[gid]} active, max {limit}"
                        ),
                    )
                )

        # Drone bay capacity
        drone_bay_used = float(fit.droneBayUsed or 0.0)
        drone_bay_total = float(fit.ship.getModifiedItemAttr("droneCapacity") or 0.0)
        if fit.drones and drone_bay_used > drone_bay_total + eps:
            issues.append(
                ValidationIssue(
                    code="drone_bay_exceeded",
                    message=f"Drone bay exceeded: {drone_bay_used:.1f} / {drone_bay_total:.1f} m3",
                )
            )

        # Explicit drone bandwidth & active drone count
        drone_bw_used = float(fit.droneBandwidthUsed or 0.0)
        drone_bw_total = float(fit.ship.getModifiedItemAttr("droneBandwidth") or 0.0)
        if fit.drones and drone_bw_used > drone_bw_total + eps:
            issues.append(
                ValidationIssue(
                    code="drone_bandwidth_exceeded",
                    message=f"Drone bandwidth exceeded: {drone_bw_used:.1f} / {drone_bw_total:.1f} Mbit/s",
                )
            )

        active_drones_used = int(fit.activeDrones or 0)
        max_active_drones = int(fit.extraAttributes["maxActiveDrones"] or 0)
        if active_drones_used > max_active_drones:
            issues.append(
                ValidationIssue(
                    code="active_drones_exceeded",
                    message=f"Active drones exceeded: {active_drones_used} active, max {max_active_drones}",
                )
            )

    return issues


def get_module_variants(module_name: str) -> list[ItemInfo]:
    """Return all published variant items for a module (same variation family or group/size)."""
    init_engine()
    with _ENGINE_LOCK:
        item = _eos_db.getItem(module_name)
        if item is None:
            return []

        slot = _Module.calculateSlot(item)
        rig_size = item.attributes.get("rigSize")
        rig_size_val = rig_size.value if rig_size is not None else None
        charge_size = item.attributes.get("chargeSize")
        charge_size_val = charge_size.value if charge_size is not None else None
        is_cap = (item.attributes.get("volume").value or 0) >= 4000 if item.attributes.get("volume") else False

        # Prefer eos variation family (varParent / varChildren), otherwise same group + matching size
        candidates_by_id: dict[int, Any] = {}

        base_item = getattr(item, "varParent", None) or item
        var_children = list(getattr(base_item, "varChildren", None) or ())
        if var_children or getattr(item, "varParent", None) is not None:
            candidates_by_id[base_item.ID] = base_item
            for v in var_children:
                candidates_by_id[v.ID] = v

        # Fall back to same-group items with matching slot & size only if no variation family exists
        if not candidates_by_id:
            group = _eos_db.getGroup(item.groupID, eager="items.attributes")
            if group is not None:
                for cand in group.items:
                    if not cand.published:
                        continue
                    if _Module.calculateSlot(cand) != slot:
                        continue
                    if rig_size_val is not None:
                        c_rs = cand.attributes.get("rigSize")
                        if c_rs is None or c_rs.value != rig_size_val:
                            continue
                    if charge_size_val is not None:
                        c_cs = cand.attributes.get("chargeSize")
                        if c_cs is None or c_cs.value != charge_size_val:
                            continue
                    c_vol = cand.attributes.get("volume")
                    c_is_cap = (c_vol.value or 0) >= 4000 if c_vol else False
                    if c_is_cap != is_cap:
                        continue
                    candidates_by_id[cand.ID] = cand

        results: list[ItemInfo] = []
        for cand in candidates_by_id.values():
            if not cand.published:
                continue
            results.append(_item_to_info(cand))

        results.sort(key=lambda x: (x.meta_level if x.meta_level is not None else 0, x.name))
        return results


def get_charge_variants(module_name: str) -> list[ItemInfo]:
    """Return all published valid charges for the given module."""
    init_engine()
    with _ENGINE_LOCK:
        item = _eos_db.getItem(module_name)
        if item is None:
            return []
        try:
            mod = _Module(item)
        except ValueError:
            return []
        valid = mod.getValidCharges()
        results = [_item_to_info(c) for c in valid if c.published]
        results.sort(key=lambda x: (x.meta_level if x.meta_level is not None else 0, x.name))
        return results
