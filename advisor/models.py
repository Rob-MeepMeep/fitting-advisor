"""Plain data models for fitting-advisor (no eos types)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SlotType = Literal["low", "med", "high", "rig", "subsystem", "service"]
ModuleState = Literal["offline", "online", "active", "overheated"]


class ModuleSpec(BaseModel):
    """A single fitted module or empty slot placeholder."""

    name: str = ""
    charge: str | None = None
    state: ModuleState | None = None
    slot: SlotType | None = None
    empty: bool = False


class DroneSpec(BaseModel):
    """A stack of drones or fighters."""

    name: str
    quantity: int = Field(ge=1)
    active: int | None = None


class CargoSpec(BaseModel):
    """A stack of cargo items."""

    name: str
    quantity: int = Field(ge=1)


class FitSpec(BaseModel):
    """Engine-independent representation of a ship fit."""

    ship: str
    name: str = "Unnamed Fit"
    lows: list[ModuleSpec] = Field(default_factory=list)
    mids: list[ModuleSpec] = Field(default_factory=list)
    highs: list[ModuleSpec] = Field(default_factory=list)
    rigs: list[ModuleSpec] = Field(default_factory=list)
    subsystems: list[ModuleSpec] = Field(default_factory=list)
    drones: list[DroneSpec] = Field(default_factory=list)
    fighters: list[DroneSpec] = Field(default_factory=list)
    cargo: list[CargoSpec] = Field(default_factory=list)

    def iter_modules(self) -> list[tuple[SlotType, ModuleSpec]]:
        """Yield (slot_type, module_spec) in canonical order (subsystems first for T3C)."""
        result: list[tuple[SlotType, ModuleSpec]] = []
        for slot_name, bucket in (
            ("subsystem", self.subsystems),
            ("low", self.lows),
            ("med", self.mids),
            ("high", self.highs),
            ("rig", self.rigs),
        ):
            for mod in bucket:
                result.append((slot_name, mod))
        return result


class SkillSet(BaseModel):
    """Character skill configuration."""

    preset: Literal["all0", "all5", "custom"] = "all5"
    character_name: str | None = None
    skills: dict[int, int] = Field(default_factory=dict)


class ResistProfile(BaseModel):
    """Damage resistance percentages (0-100)."""

    em: float
    thermal: float
    kinetic: float
    explosive: float


class FittedModuleInfo(BaseModel):
    """Resolved module info from the engine."""

    slot: str
    name: str
    type_id: int | None = None
    charge: str | None = None
    charge_type_id: int | None = None
    state: str = "online"
    meta_group: str | None = None
    empty: bool = False


class FitStats(BaseModel):
    """All fit statistics computed exclusively by Pyfa's eos engine."""

    ship: str
    ship_type_id: int
    fit_name: str

    # Offense
    dps_total: float
    dps_weapon: float
    dps_drone: float
    volley_total: float
    volley_weapon: float
    volley_drone: float

    # Defense
    ehp_total: float
    ehp_shield: float
    ehp_armor: float
    ehp_hull: float
    hp_shield: float
    hp_armor: float
    hp_hull: float
    resists_shield: ResistProfile
    resists_armor: ResistProfile
    resists_hull: ResistProfile
    tank_passive_shield: float
    tank_shield_repair: float
    tank_armor_repair: float
    tank_hull_repair: float

    # Resources
    cpu_used: float
    cpu_output: float
    pg_used: float
    pg_output: float
    calibration_used: float
    calibration_output: float

    # Navigation & Targeting
    max_speed: float
    align_time: float
    warp_speed: float
    signature_radius: float
    mass: float

    # Capacitor
    cap_capacity: float
    cap_stable: bool
    cap_stable_pct: float | None = None
    cap_duration_s: float | None = None
    cap_used: float
    cap_recharge: float
    cap_delta: float

    # Drones
    drone_bandwidth_used: float
    drone_bandwidth_total: float
    drone_bay_used: float
    drone_bay_total: float

    # Slots & Hardpoints
    slots_total: dict[str, int]
    slots_used: dict[str, int]
    turrets_used: int
    turrets_total: int
    launchers_used: int
    launchers_total: int

    # Resolved modules for UI rendering
    modules: list[FittedModuleInfo] = Field(default_factory=list)

    # Versions
    pyfa_version: str
    gamedata_version: str


class ItemInfo(BaseModel):
    """Metadata for an EVE item from gamedata."""

    type_id: int
    name: str
    group_id: int
    group_name: str
    category_name: str
    slot: str | None = None
    published: bool = True
    meta_group: str = "Tech I"
    meta_level: int | None = None
    volume: float | None = None
    rig_size: int | None = None
    charge_size: int | None = None


class ValidationIssue(BaseModel):
    """A single fit legality violation."""

    code: str
    message: str
    module_name: str | None = None


class ValidationResult(BaseModel):
    """Result of validating a fit."""

    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
