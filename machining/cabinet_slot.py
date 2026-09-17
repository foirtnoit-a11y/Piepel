"""Tapered finger notch in a cabinet front, cut on a StepCraft router.

The feature is the one on the `kastje` drawing: a trapezoidal notch opening on
the edge of the front, wider at the mouth than at its floor, with the same
corner radius at all four corners.

                     <---------- 71.651 ---------->     mouth span, over the rounds
                     <-------- 65.000 -------->         mouth width, sharp corners
        ____________/                        \\____________   panel edge, Y0
                   /                          \\
                  |    R6 at all four corners  |          20.000 deep
                   \\__________________________/
                    <------- 40.000 ------->              floor width, sharp corners

The three width figures on a drawing like this are not independent, which is
what makes the reading checkable. A radius R tangent to both the panel edge and
a flank standing at phi to that edge backs its tangent point off the sharp
corner by

    t = R * tan(phi / 2)

and the same t appears at the floor, where the flank meets the floor line at
180 - phi and the fillet centre sits in the waste instead of in the material.
With phi = atan(20 / 12.5) = 57.9946 deg and R = 6:

    t = 6 * tan(28.9973 deg) = 3.3255
    mouth span = 65 + 2t = 71.651     <- the 71.65 on the drawing
    floor span = 40 - 2t = 33.349

So 71.65 is a derived number, not an independent one, and it agreeing to two
decimals is the evidence that the drawing has been read the way it was meant.

Coordinates follow the rest of the repo: millimetres, Z0 on the top face with
material into -Z. X0 is the notch centreline and Y0 is the panel edge the notch
opens on, with the panel extending into +Y. That puts the whole cut in positive
Y and symmetric X, and it is a datum the operator can actually find: touch off
on the edge, halve the notch.

The toolpath is emitted on the compensated centreline -- the offset is computed
here, from the cutter diameter you measured, not left to G41/G42 on the
control. That is deliberate. Cutter compensation on a hobby control has to
guess lead-ins, differs between versions, and defeats the verifier, which can
only backplot the path that is actually in the file. Measure the cutter, pass
--cutter, and what you read in the program is what the tool centre does.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

Point = tuple[float, float]

# --------------------------------------------------------------------------
# Every dimension the drawing carries, named once. Nothing below is a bare
# number: the CLI defaults come from here and the header of the emitted
# program prints them back so the operator can check them against the paper.
# --------------------------------------------------------------------------
PARAMETERS: dict[str, tuple[float, str]] = {
    # -- the notch, from the drawing --
    "mouth_width":     (65.0,  "width at the panel edge, to the sharp corners"),
    "floor_width":     (40.0,  "width at the bottom of the notch, sharp corners"),
    "depth":           (20.0,  "how far the notch reaches in from the edge"),
    "corner_radius":   (6.0,   "R6, all four corners"),
    # -- the material --
    "thickness":       (12.0,  "MDF panel thickness; the notch is cut through it"),
    "panel_width":     (600.0, "stock X extent, notch centred; verification only"),
    "panel_height":    (400.0, "stock Y extent from the notched edge; verification only"),
    "spoilboard_bite": (0.5,   "how far past the panel the last pass goes"),
    # -- the tool --
    "cutter_diameter": (8.0,   "measured diameter of the cutter, not its label"),
    "flutes":          (2.0,   "flute count, for the chip load"),
    "stickout":        (30.0,  "collet face to tip, for the deflection estimate"),
    # -- the cut --
    "stepdown":        (3.0,   "maximum axial depth per pass; passes are evened out"),
    "feed_scale":      (0.8,   "fraction of the calculated feed to program first off"),
    "safe_z":          (5.0,   "rapid plane above the top face"),
    "plunge_feed":     (400.0, "straight-down feed, ~1/6 of the lateral feed"),
    "lead_in":         (2.0,   "how far clear of the edge the tool plunges"),
    # -- the tabs holding the slug --
    "tab_width":       (8.0,   "flat length of each tab along the path"),
    "tab_height":      (1.5,   "material left under the cut at a tab"),
    "tab_ramp":        (2.0,   "ramp length on each side of a tab"),
}


def _p(name: str) -> float:
    return PARAMETERS[name][0]


# --------------------------------------------------------------------------
# path primitives
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    p0: Point
    p1: Point
    tabbable: bool = False

    @property
    def length(self) -> float:
        return math.dist(self.p0, self.p1)

    def at(self, s: float) -> Point:
        """Point s mm along the segment from p0."""
        f = s / self.length if self.length else 0.0
        return (self.p0[0] + (self.p1[0] - self.p0[0]) * f,
                self.p0[1] + (self.p1[1] - self.p0[1]) * f)


@dataclass(frozen=True)
class Arc:
    p0: Point
    p1: Point
    center: Point
    ccw: bool
    tabbable: bool = False

    @property
    def radius(self) -> float:
        return math.dist(self.center, self.p0)

    @property
    def sweep(self) -> float:
        """Signed sweep in radians: positive counter-clockwise."""
        a0 = math.atan2(self.p0[1] - self.center[1], self.p0[0] - self.center[0])
        a1 = math.atan2(self.p1[1] - self.center[1], self.p1[0] - self.center[0])
        s = a1 - a0
        if self.ccw:
            while s <= 0:
                s += 2 * math.pi
        else:
            while s >= 0:
                s -= 2 * math.pi
        return s

    @property
    def length(self) -> float:
        return abs(self.sweep) * self.radius

    @property
    def radius_error(self) -> float:
        """How far the two endpoints disagree about the radius."""
        return abs(math.dist(self.center, self.p1) - self.radius)


Segment = Line | Arc


def _mirror(p: Point) -> Point:
    return (-p[0], p[1])


# --------------------------------------------------------------------------
# the feature
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Notch:
    """The trapezoidal notch as drawn, in part coordinates."""

    mouth_width: float = _p("mouth_width")
    floor_width: float = _p("floor_width")
    depth: float = _p("depth")
    corner_radius: float = _p("corner_radius")

    def __post_init__(self):
        if self.depth <= 0:
            raise ValueError("depth must be positive")
        if self.mouth_width <= self.floor_width:
            raise ValueError(
                f"the mouth ({self.mouth_width:g}) must be wider than the floor "
                f"({self.floor_width:g}); a notch that narrows towards the edge "
                f"is undercut and no straight cutter can make it")
        if self.corner_radius < 0:
            raise ValueError("corner radius cannot be negative")
        if 2 * self.tangent >= self.floor_width:
            raise ValueError(
                f"R{self.corner_radius:g} is too big for a {self.floor_width:g} mm "
                f"floor: the two floor fillets would overlap")
        if 2 * self.tangent >= self.flank_length:
            raise ValueError(
                f"R{self.corner_radius:g} eats the whole {self.flank_length:.3f} mm "
                f"flank; there would be no straight left between the fillets")

    # -- derived geometry --
    @property
    def half_mouth(self) -> float:
        return self.mouth_width / 2.0

    @property
    def half_floor(self) -> float:
        return self.floor_width / 2.0

    @property
    def flare(self) -> float:
        """How far each flank moves outward between floor and mouth."""
        return self.half_mouth - self.half_floor

    @property
    def flank_angle(self) -> float:
        """Angle between a flank and the panel edge, radians."""
        return math.atan2(self.depth, self.flare)

    @property
    def flank_length(self) -> float:
        """Sharp corner to sharp corner along a flank."""
        return math.hypot(self.flare, self.depth)

    @property
    def tangent(self) -> float:
        """R * tan(phi/2): how far each fillet backs off its sharp corner."""
        return self.corner_radius * math.tan(self.flank_angle / 2.0)

    @property
    def mouth_span(self) -> float:
        """Across the mouth, tangent point to tangent point -- the 71.65."""
        return self.mouth_width + 2 * self.tangent

    @property
    def floor_span(self) -> float:
        """The flat left on the floor between the two fillets."""
        return self.floor_width - 2 * self.tangent

    @property
    def flank_flat(self) -> float:
        """The straight left on a flank between its two fillets."""
        return self.flank_length - 2 * self.tangent

    # -- the toolpath --
    def toolpath(self, tool_radius: float, lead_in: float) -> list[Segment]:
        """Tool-centre path, offset into the waste by `tool_radius`.

        An open contour: it starts clear of the panel edge, runs up the left
        flank, across the floor and back out, so the cutter never has to enter
        the material by plunging into it.
        """
        r, R = tool_radius, self.corner_radius
        if r <= 0:
            raise ValueError("cutter radius must be positive")
        if r >= R:
            raise ValueError(
                f"a {2 * r:g} mm cutter cannot cut an R{R:g} inside corner; "
                f"use {2 * R:g} mm or smaller")
        if 2 * r >= self.floor_span + 2 * R:
            raise ValueError(
                f"a {2 * r:g} mm cutter will not fit in a {self.floor_width:g} mm floor")

        a, b, h, t = self.half_mouth, self.half_floor, self.depth, self.tangent
        L = self.flank_length
        # Left flank: unit direction mouth->floor, and the unit normal pointing
        # into the waste (towards the notch centreline).
        d = (self.flare / L, h / L)
        n = (h / L, -self.flare / L)

        c_mouth = (-(a + t), R)      # fillet centre, in the material
        c_floor = (-(b - t), h - R)  # fillet centre, in the waste

        p0 = (-(a + t), -r)                                    # on the offset edge
        p1 = (c_mouth[0] + (R + r) * n[0], c_mouth[1] + (R + r) * n[1])
        p2 = (c_floor[0] - (R - r) * n[0], c_floor[1] - (R - r) * n[1])
        p3 = (-(b - t), h - r)                                 # on the offset floor

        entry = (p0[0], -(r + lead_in))

        return [
            Line(entry, p0),
            Arc(p0, p1, c_mouth, ccw=True),                    # round the mouth corner
            Line(p1, p2, tabbable=True),                       # left flank
            Arc(p2, p3, c_floor, ccw=False),                   # into the floor
            Line(p3, _mirror(p3)),                             # the floor
            Arc(_mirror(p3), _mirror(p2), _mirror(c_floor), ccw=False),
            Line(_mirror(p2), _mirror(p1), tabbable=True),     # right flank
            Arc(_mirror(p1), _mirror(p0), _mirror(c_mouth), ccw=True),
            Line(_mirror(p0), _mirror(entry)),
        ]


@dataclass(frozen=True)
class Panel:
    """The board the notch is cut in."""

    thickness: float = _p("thickness")
    width: float = _p("panel_width")
    height: float = _p("panel_height")
    spoilboard_bite: float = _p("spoilboard_bite")

    @property
    def z_through(self) -> float:
        """Depth the last pass reaches: through the board and a skim beyond."""
        return -(self.thickness + self.spoilboard_bite)


@dataclass
class CutPlan:
    """Everything about how the notch gets cut, as opposed to what it is."""

    cutter_diameter: float = _p("cutter_diameter")
    flutes: int = int(_p("flutes"))
    stickout: float = _p("stickout")
    rpm: float = 0.0            # 0 = derive from the feeds calculator
    feed: float = 0.0           # 0 = derive
    plunge_feed: float = _p("plunge_feed")
    stepdown: float = _p("stepdown")
    feed_scale: float = _p("feed_scale")
    safe_z: float = _p("safe_z")
    lead_in: float = _p("lead_in")
    tab_width: float = _p("tab_width")
    tab_height: float = _p("tab_height")
    tab_ramp: float = _p("tab_ramp")
    machine: str = "stepcraft_m1000"
    derivation: list[str] = field(default_factory=list)

    @property
    def tool_radius(self) -> float:
        return self.cutter_diameter / 2.0

    @property
    def tab_footprint(self) -> float:
        return self.tab_width + 2 * self.tab_ramp


def pass_depths(panel: Panel, stepdown: float) -> list[float]:
    """Evenly spread passes, never deeper than `stepdown`.

    Evening them out matters: a leftover 0.4 mm skim as the final pass is the
    one that grabs, because the cutter is buried to full depth with almost no
    chip to take.
    """
    if stepdown <= 0:
        raise ValueError("stepdown must be positive")
    total = -panel.z_through
    n = max(1, math.ceil(total / stepdown - 1e-9))
    step = total / n
    return [-step * (i + 1) for i in range(n)]


def derive_feed(plan: CutPlan, panel: Panel, machine) -> None:
    """Fill in rpm/feed from feeds.py, honouring the machine's limits."""
    import feeds

    if plan.rpm and plan.feed:
        return
    depths = pass_depths(panel, plan.stepdown)
    res = feeds.compute(feeds.Cut(
        material="mdf",
        diameter=plan.cutter_diameter,
        flutes=plan.flutes,
        ae=plan.cutter_diameter,          # a contour cut is a full slot
        ap=abs(depths[0]),
        stickout=plan.stickout,
        max_rpm=machine.max_rpm,
        max_feed=machine.max_feed_mm_min,
    ))
    if not plan.rpm:
        plan.rpm = round(res.rpm, -2)
    if not plan.feed:
        plan.feed = round(res.feed * plan.feed_scale, -1)
    plan.derivation = (
        [f"feeds.py mdf d{plan.cutter_diameter:g} f{plan.flutes} "
         f"ae{plan.cutter_diameter:g} ap{abs(depths[0]):.3f} -> "
         f"S{res.rpm:.0f} F{res.feed:.0f}",
         f"programmed at {plan.feed_scale:g} of that: F{plan.feed:.0f} "
         f"({res.fz_commanded * plan.feed_scale:.3f} mm/tooth)",
         f"{res.power_kw:.2f} kW at the cutter, "
         f"{res.deflection * 1000:.0f} um tip deflection"]
        + [f"CLAMPED {c}" for c in res.clamped]
        + [f"! {w}" for w in res.warnings]
    )


# --------------------------------------------------------------------------
# emitting the program
# --------------------------------------------------------------------------
def _f(v: float) -> str:
    """Three decimals is a micron; trailing zeros just make the file noisy."""
    s = f"{v:.3f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _r(v: float) -> str:
    """Feeds and speeds: whole numbers, because that is all they are worth."""
    return f"{v:.0f}"


class _Writer:
    def __init__(self, feed: float):
        self.lines: list[str] = []
        self.feed = feed
        self._f_active: float | None = None

    def comment(self, text: str) -> None:
        self.lines.append(f"({text})")

    def raw(self, text: str) -> None:
        self.lines.append(text)

    def rapid(self, *, x=None, y=None, z=None) -> None:
        self.lines.append("G0" + self._axes(x, y, z))
        self._f_active = None if False else self._f_active

    def feed_to(self, *, x=None, y=None, z=None, feed=None) -> None:
        f = feed if feed is not None else self.feed
        word = "" if self._f_active == f else f" F{_r(f)}"
        self._f_active = f
        self.lines.append("G1" + self._axes(x, y, z) + word)

    def arc_to(self, p1: Point, center: Point, start: Point, ccw: bool) -> None:
        i, j = center[0] - start[0], center[1] - start[1]
        word = "" if self._f_active == self.feed else f" F{_r(self.feed)}"
        self._f_active = self.feed
        self.lines.append(
            f"{'G3' if ccw else 'G2'} X{_f(p1[0])} Y{_f(p1[1])} "
            f"I{_f(i)} J{_f(j)}{word}")

    @staticmethod
    def _axes(x, y, z) -> str:
        out = ""
        for letter, v in (("X", x), ("Y", y), ("Z", z)):
            if v is not None:
                out += f" {letter}{_f(v)}"
        return out


def _emit_line(w: _Writer, seg: Line, z: float, tab_z: float | None,
               plan: CutPlan) -> None:
    """One straight segment, lifting over its tab if this pass is deep enough."""
    if tab_z is None or not seg.tabbable or z >= tab_z - 1e-9:
        w.feed_to(x=seg.p1[0], y=seg.p1[1])
        return
    mid = seg.length / 2.0
    half = plan.tab_width / 2.0
    ramp = plan.tab_ramp
    marks = [mid - half - ramp, mid - half, mid + half, mid + half + ramp]
    heights = [z, tab_z, tab_z, z]
    for s, zz in zip(marks, heights):
        p = seg.at(s)
        w.feed_to(x=p[0], y=p[1], z=zz)
    w.feed_to(x=seg.p1[0], y=seg.p1[1])


def program(notch: Notch, panel: Panel, plan: CutPlan, machine) -> str:
    """The whole NC program, as text."""
    path = notch.toolpath(plan.tool_radius, plan.lead_in)
    flank = next(s for s in path if isinstance(s, Line) and s.tabbable)
    if plan.tab_footprint > flank.length:
        raise ValueError(
            f"a tab needs {plan.tab_footprint:g} mm of straight flank but the "
            f"offset flank is only {flank.length:.3f} mm; shorten the tab or "
            f"drop --tab-width to 0")
    depths = pass_depths(panel, plan.stepdown)
    tab_z = (-panel.thickness + plan.tab_height) if plan.tab_width > 0 else None
    entry = path[0].p0

    w = _Writer(plan.feed)
    w.comment("cabinet front - tapered finger notch")
    w.comment(f"drawing: mouth {_f(notch.mouth_width)} over sharp corners, "
              f"{_f(notch.mouth_span)} over the R{_f(notch.corner_radius)} rounds")
    w.comment(f"         floor {_f(notch.floor_width)}, depth {_f(notch.depth)}, "
              f"flank {math.degrees(notch.flank_angle):.2f} deg to the edge")
    w.comment(f"machine: {machine.name}")
    w.comment("datum:   X0 = notch centreline, Y0 = the panel edge it opens on,")
    w.comment("         Z0 = TOP FACE of the panel, material into -Z")
    w.comment(f"stock:   {_f(panel.thickness)} mm MDF on a sacrificial board")
    w.comment(f"tool:    {_f(plan.cutter_diameter)} mm {plan.flutes}-flute, "
              f"path is on the compensated centreline - no G41/G42")
    w.comment(f"cut:     {len(depths)} passes of {_f(abs(depths[0]))}, through to "
              f"Z{_f(panel.z_through)} ({_f(panel.spoilboard_bite)} into the board)")
    if tab_z is not None:
        w.comment(f"tabs:    2 x {_f(plan.tab_width)} wide, {_f(plan.tab_height)} "
                  f"high (top at Z{_f(tab_z)}), one per flank")
    else:
        w.comment("tabs:    NONE - the slug comes loose on the last pass")
    w.comment(f"feeds:   S{_r(plan.rpm)} F{_r(plan.feed)}, "
              f"plunge F{_r(plan.plunge_feed)}")
    for line in plan.derivation:
        w.comment(f"  {line}")
    w.comment("verify:  python3 -m gcode.cli <this file> -s <setup.json>")
    w.comment("SET THE SPINDLE DIAL to the S value if WinPC-NC does not drive it")

    w.raw("G21 G17 G40 G90 G94")
    w.raw("G54")
    w.raw(f"M3 S{_r(plan.rpm)}")
    w.rapid(z=plan.safe_z)
    w.rapid(x=entry[0], y=entry[1])

    z_prev = 0.0
    for i, z in enumerate(depths):
        w.comment(f"pass {i + 1} of {len(depths)} - Z{_f(z)}")
        w.rapid(z=z_prev + 1.0)
        w.feed_to(z=z, feed=plan.plunge_feed)
        for seg in path:
            if isinstance(seg, Line):
                _emit_line(w, seg, z, tab_z, plan)
            else:
                w.arc_to(seg.p1, seg.center, seg.p0, seg.ccw)
        w.rapid(z=plan.safe_z)
        if i + 1 < len(depths):
            w.rapid(x=entry[0], y=entry[1])
        z_prev = z

    w.raw("M5")
    w.raw("M30")
    return "\n".join(w.lines) + "\n"


def setup_dict(notch: Notch, panel: Panel, plan: CutPlan) -> dict:
    """The contract the verifier checks the program against.

    The stock bottom is the *spoilboard* face, not the panel face, because the
    last pass is meant to go past the panel -- a through cut that stops exactly
    at the panel bottom leaves a skin. Declaring it honestly is what keeps
    CUT_BELOW_STOCK meaningful for the mistakes it is actually there to catch.
    """
    return {
        "machine": plan.machine,
        "expected_wcs": ["G54"],
        "stock": {
            "x_min": -panel.width / 2.0, "x_max": panel.width / 2.0,
            "y_min": 0.0, "y_max": panel.height,
            "z_bottom": panel.z_through, "z_top": 0.0,
            "clearance": plan.safe_z,
        },
        "tools": [{
            "number": 1,
            "diameter": plan.cutter_diameter,
            "flutes": plan.flutes,
            "stickout": plan.stickout,
            "description": f"{plan.cutter_diameter:g} mm {plan.flutes}FL "
                           f"straight router cutter",
        }],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None) -> int:
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gcode.machine import Machine

    ap = argparse.ArgumentParser(
        prog="cabinet_slot",
        description="Tapered finger notch in a cabinet front, for a StepCraft "
                    "router running WinPC-NC.")
    for name, (default, help_text) in PARAMETERS.items():
        ap.add_argument(f"--{name.replace('_', '-')}", type=float, default=default,
                        help=f"{help_text} (default {default:g})")
    ap.add_argument("-m", "--machine", default="stepcraft_m1000",
                    help="machine profile (default stepcraft_m1000)")
    ap.add_argument("-S", "--rpm", type=float, default=0.0,
                    help="spindle speed; 0 derives it from feeds.py")
    ap.add_argument("-F", "--feed", type=float, default=0.0,
                    help="cutting feed mm/min; 0 derives it from feeds.py")
    ap.add_argument("-o", "--out", help="write the NC program here")
    ap.add_argument("--setup", help="write the verifier setup JSON here")
    a = ap.parse_args(argv)

    notch = Notch(a.mouth_width, a.floor_width, a.depth, a.corner_radius)
    panel = Panel(a.thickness, a.panel_width, a.panel_height, a.spoilboard_bite)
    plan = CutPlan(
        cutter_diameter=a.cutter_diameter, flutes=int(a.flutes),
        stickout=a.stickout, rpm=a.rpm, feed=a.feed,
        plunge_feed=a.plunge_feed, stepdown=a.stepdown, feed_scale=a.feed_scale,
        safe_z=a.safe_z, lead_in=a.lead_in, tab_width=a.tab_width,
        tab_height=a.tab_height, tab_ramp=a.tab_ramp, machine=a.machine,
    )
    machine = Machine.load(a.machine)
    derive_feed(plan, panel, machine)

    if panel.thickness >= notch.depth:
        print(f"note: the panel ({panel.thickness:g} mm) is thicker than the notch "
              f"is deep ({notch.depth:g} mm) - check that a through cut is what "
              f"you want", file=sys.stderr)

    text = program(notch, panel, plan, machine)
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text, end="")
    if a.setup:
        Path(a.setup).write_text(json.dumps(setup_dict(notch, panel, plan), indent=2) + "\n")
        print(f"wrote {a.setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
