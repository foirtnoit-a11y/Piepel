"""Machine + stock definitions the verifier checks a program against."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

MM_PER_INCH = 25.4

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


@dataclass
class Envelope:
    """Travel limits in machine coordinates, mm. Z is negative-down from home."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    def contains(self, x: float, y: float, z: float, tol: float = 0.0) -> bool:
        return (
            self.x_min - tol <= x <= self.x_max + tol
            and self.y_min - tol <= y <= self.y_max + tol
            and self.z_min - tol <= z <= self.z_max + tol
        )


@dataclass
class Machine:
    name: str
    control: str  # 'haas' | 'fanuc'
    envelope: Envelope
    max_rpm: float
    max_feed_mm_min: float
    rapid_mm_min: float
    tool_capacity: int
    tool_change_seconds: float = 5.0
    has_rigid_tapping: bool = True
    has_high_speed_lookahead: bool = True
    # Codes this control does not accept; flagged as hard errors.
    unsupported_g: tuple[float, ...] = ()
    unsupported_m: tuple[float, ...] = ()

    @classmethod
    def load(cls, name_or_path: str) -> "Machine":
        p = Path(name_or_path)
        if not p.exists():
            p = PROFILE_DIR / f"{name_or_path}.json"
        if not p.exists():
            avail = ", ".join(sorted(f.stem for f in PROFILE_DIR.glob("*.json")))
            raise FileNotFoundError(f"no machine profile {name_or_path!r}; have: {avail}")
        data = json.loads(p.read_text())
        env = Envelope(**data.pop("envelope"))
        data["unsupported_g"] = tuple(data.get("unsupported_g", ()))
        data["unsupported_m"] = tuple(data.get("unsupported_m", ()))
        return cls(envelope=env, **data)

    def to_json(self) -> str:
        d = asdict(self)
        d["unsupported_g"] = list(self.unsupported_g)
        d["unsupported_m"] = list(self.unsupported_m)
        return json.dumps(d, indent=2)


@dataclass
class Stock:
    """Stock block in *work* coordinates (i.e. relative to the active WCS).

    The usual convention, and what the Fusion side emits: part datum at a
    corner or centre of the stock with Z0 on the top face, so `z_top` is 0.0
    and `z_bottom` is negative.
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_bottom: float
    z_top: float = 0.0
    # Anything above this is considered safe air; rapids are fine here.
    clearance: float = 5.0

    def is_inside_xy(self, x: float, y: float, tol: float = 0.0) -> bool:
        return self.x_min - tol <= x <= self.x_max + tol and self.y_min - tol <= y <= self.y_max + tol

    @property
    def safe_z(self) -> float:
        return self.z_top + self.clearance


@dataclass
class Tool:
    """What the operator loaded, so the verifier can sanity-check the program."""

    number: int
    diameter: float  # mm
    description: str = ""
    flutes: int = 2
    stickout: float = 0.0  # mm below holder; 0 = unknown
    length_offset: int | None = None  # expected H number; defaults to `number`
    corner_radius: float = 0.0

    @property
    def expected_h(self) -> int:
        return self.length_offset if self.length_offset is not None else self.number


@dataclass
class Setup:
    """Everything the verifier needs beyond the NC file itself."""

    machine: Machine
    stock: Stock | None = None
    tools: dict[int, Tool] = field(default_factory=dict)
    # Work offset the part is dialled in on. Extra offsets are flagged.
    expected_wcs: tuple[str, ...] = ("G54",)
