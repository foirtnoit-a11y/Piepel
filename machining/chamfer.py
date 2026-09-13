"""Chamfer path for a lengthwise slot in a round shaft, cut with a 2D toolpath.

The problem: the top edge of a slot in a shaft does not lie in a plane. On a
50 mm shaft with a 12 mm slot the edge sits sqrt(25^2 - 6^2) = 24.2693 mm from
the axis, so 0.7307 mm below the top of the shaft -- but at the rounded end of
the slot the edge climbs all the way back up to the top. A 2D contour runs at
one Z, so it cannot follow that.

The fix exploits the fact that a conical chamfer tool trades height for radius.
Being dz too high is the same as being dz*tan(a) too far out, so the Z error
can be cancelled by offsetting the path outward by exactly the local drop:

    offset(x) = (R - sqrt(R^2 - x^2)) * tan(a)

At the straight flanks x is constant, so that is a fixed offset and the result
is exact. Around the rounded ends x sweeps from W/2 to 0 and the offset has to
shrink with it.

The common shop approximation -- take the slot outline, widen it by twice the
maximum drop, keep the length and corner radius -- is the same curve translated
sideways. It is exact at both ends of each arc and wrong in between, because
the required offset grows as x^2/2R while a translation ramps it linearly in x.
The overshoot peaks at drop_max*tan(a)/4 around 60 degrees into each end arc.
This module reports that number, so you can decide whether it matters against
the chamfer you are cutting, and emits the exact curve as DXF when it does.

Conventions follow the rest of the repo: millimetres, and Z0 is the top of the
shaft with material going into -Z.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from dxf import Dxf, arc_segments, chordal_error

END_STYLES = ("round", "square", "open")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------
@dataclass
class Shaft:
    diameter: float

    @property
    def radius(self) -> float:
        return self.diameter / 2.0

    def drop(self, x: float) -> float:
        """How far below the top of the shaft the surface sits at offset x."""
        r = self.radius
        if abs(x) > r:
            raise ValueError(f"x={x:g} is off the {self.diameter:g} mm shaft")
        return r - math.sqrt(r * r - x * x)


@dataclass
class Slot:
    width: float
    length: float = 0.0          # ignored when end_style is "open"
    end_style: str = "round"     # round = cut with a W-diameter tool
    depth: float | None = None   # only used for sanity checks

    def __post_init__(self):
        if self.end_style not in END_STYLES:
            raise ValueError(f"end_style must be one of {END_STYLES}")
        if self.width <= 0:
            raise ValueError("slot width must be positive")
        if self.end_style == "round" and self.length < self.width:
            raise ValueError(
                f"a round-ended slot cannot be shorter than it is wide "
                f"({self.length:g} < {self.width:g} mm)")
        if self.end_style != "open" and self.length <= 0:
            raise ValueError("slot length must be positive unless the slot is open-ended")

    @property
    def half_width(self) -> float:
        return self.width / 2.0

    @property
    def arc_center_y(self) -> float:
        """Y of the end-arc centre for a round-ended slot."""
        return self.length / 2.0 - self.half_width


@dataclass
class ChamferTool:
    included_angle: float = 90.0  # as engraved on the tool; 90 deg is the usual
    diameter: float = 6.0
    tip_diameter: float = 0.0     # physical flat at the point, if any
    flutes: int = 4

    @property
    def half_angle(self) -> float:
        """Flank angle from the tool axis, in radians. 45 deg for a 90 deg tool."""
        return math.radians(self.included_angle / 2.0)

    @property
    def tan_half(self) -> float:
        return math.tan(self.half_angle)

    @property
    def tip_rise(self) -> float:
        """How far the physical flat sits above the virtual cone apex."""
        if self.tip_diameter <= 0:
            return 0.0
        return (self.tip_diameter / 2.0) / self.tan_half


# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------
Segment = tuple[str, list[tuple[float, float]]]  # ("line" | "poly", points)


@dataclass
class ChamferPath:
    shaft: Shaft
    slot: Slot
    tool: ChamferTool
    leg: float                    # horizontal (radial) chamfer leg, mm
    segments: list[Segment]
    z_apex: float                 # Z for the virtual cone apex
    drop_max: float
    offset_at_flank: float
    simple: dict
    chord_error: float
    warnings: list[str] = field(default_factory=list)

    @property
    def leg_vertical(self) -> float:
        return self.leg / self.tool.tan_half

    @property
    def face_width(self) -> float:
        """Width of the chamfer face itself, measured along the bevel."""
        return math.hypot(self.leg, self.leg_vertical)

    @property
    def z_flat(self) -> float:
        """Z to program if the tool offset is set to the physical flat tip."""
        return self.z_apex + self.tool.tip_rise

    @property
    def closed(self) -> bool:
        return self.slot.end_style != "open"

    def points(self) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for _, pts in self.segments:
            for p in pts:
                if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
                    out.append(p)
        return out

    def continuity(self) -> tuple[float, float]:
        """Worst gap where one entity hands over to the next, and the closing gap.

        This is what decides whether the DXF chains into a single contour. A CAM
        package that cannot match the next entity's start point to the previous
        one's end either breaks the chain or quietly leaves a gap in the
        chamfer, so both numbers want to be zero -- not merely small.
        """
        if len(self.segments) < 2 or not self.closed:
            return (0.0, 0.0)  # open slots are separate chains by design
        worst = max(math.dist(self.segments[i][1][-1], self.segments[i + 1][1][0])
                    for i in range(len(self.segments) - 1))
        closing = (math.dist(self.segments[-1][1][-1], self.segments[0][1][0])
                   if self.closed else 0.0)
        return (worst, closing)

    def path_length(self) -> float:
        pts = self.points()
        if self.closed and pts:
            pts = pts + [pts[0]]
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


# --------------------------------------------------------------------------
# path construction
# --------------------------------------------------------------------------
def _offset(shaft: Shaft, tool: ChamferTool, x: float) -> float:
    """Outward offset that cancels the Z error at this x."""
    return shaft.drop(x) * tool.tan_half


def _sample_arc(shaft, slot, tool, cx, cy, start_deg, end_deg, tol):
    """Offset points along one end arc, offset shrinking as the edge climbs."""
    hw = slot.half_width
    n = arc_segments(hw, abs(end_deg - start_deg), tol)
    # An even count puts a sample exactly on the arc's midpoint, which for a
    # 180 degree end arc is the apex -- the one point where the offset is zero
    # and the path must touch the true outline.
    n += n % 2
    pts = []
    for i in range(n + 1):
        deg = start_deg + (end_deg - start_deg) * i / n
        a = math.radians(deg)
        nx, ny = math.cos(a), math.sin(a)
        x, y = cx + hw * nx, cy + hw * ny
        o = _offset(shaft, tool, x)
        pts.append((x + o * nx, y + o * ny))
    return pts, chordal_error(hw, abs(end_deg - start_deg), n)


def _corner_arc(cx, cy, radius, start_deg, end_deg, tol):
    """Round join at a sharp slot corner.

    At a square corner the edge turns 90 degrees in plan, so the offset path
    has to sweep round it at constant distance -- the offset magnitude is the
    same on both sides (drop(W/2)*tan(a)), so a quarter circle centred on the
    true corner joins them exactly. Without this the flank offset (in X) and
    the end offset (in Y) never meet and the contour will not chain.
    """
    n = max(arc_segments(radius, abs(end_deg - start_deg), tol), 2)
    pts = []
    for i in range(n + 1):
        a = math.radians(start_deg + (end_deg - start_deg) * i / n)
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def _sample_end_line(shaft, slot, tool, y, x_from, x_to, ny, tol):
    """Offset points along a square end, which becomes a curve once offset."""
    hw = slot.half_width
    # The offset varies as x^2, so sample on curvature rather than uniformly:
    # reuse the arc sampler's density against the shaft radius.
    n = max(arc_segments(shaft.radius, math.degrees(2 * math.asin(min(hw / shaft.radius, 1.0))),
                         tol), 8)
    pts = []
    for i in range(n + 1):
        x = x_from + (x_to - x_from) * i / n
        pts.append((x, y + ny * _offset(shaft, tool, x)))
    return pts


def build(shaft: Shaft, slot: Slot, tool: ChamferTool, leg: float,
          chordal_tol: float = 0.005) -> ChamferPath:
    """Compute the compensated 2D chamfer path."""
    if leg <= 0:
        raise ValueError("chamfer leg must be positive")
    if slot.width >= shaft.diameter:
        raise ValueError(f"a {slot.width:g} mm slot does not fit a "
                         f"{shaft.diameter:g} mm shaft")

    hw = slot.half_width
    drop_max = shaft.drop(hw)
    off_flank = drop_max * tool.tan_half
    segments: list[Segment] = []
    chord_err = 0.0

    if slot.end_style == "open":
        # Two independent straight passes; the offset never varies.
        half = slot.length / 2.0 if slot.length > 0 else shaft.diameter
        segments.append(("line", [(hw + off_flank, -half), (hw + off_flank, half)]))
        segments.append(("line", [(-hw - off_flank, -half), (-hw - off_flank, half)]))
    elif slot.end_style == "round":
        c = slot.arc_center_y
        segments.append(("line", [(hw + off_flank, -c), (hw + off_flank, c)]))
        pts, e = _sample_arc(shaft, slot, tool, 0.0, c, 0.0, 180.0, chordal_tol)
        segments.append(("poly", pts))
        chord_err = max(chord_err, e)
        segments.append(("line", [(-hw - off_flank, c), (-hw - off_flank, -c)]))
        pts, e = _sample_arc(shaft, slot, tool, 0.0, -c, 180.0, 360.0, chordal_tol)
        segments.append(("poly", pts))
        chord_err = max(chord_err, e)
    else:  # square
        hl = slot.length / 2.0
        o = off_flank
        segments.append(("line", [(hw + o, -hl), (hw + o, hl)]))
        segments.append(("poly", _corner_arc(hw, hl, o, 0.0, 90.0, chordal_tol)))
        segments.append(("poly", _sample_end_line(shaft, slot, tool, hl, hw, -hw, 1.0,
                                                  chordal_tol)))
        segments.append(("poly", _corner_arc(-hw, hl, o, 90.0, 180.0, chordal_tol)))
        segments.append(("line", [(-hw - o, hl), (-hw - o, -hl)]))
        segments.append(("poly", _corner_arc(-hw, -hl, o, 180.0, 270.0, chordal_tol)))
        segments.append(("poly", _sample_end_line(shaft, slot, tool, -hl, -hw, hw, -1.0,
                                                  chordal_tol)))
        segments.append(("poly", _corner_arc(hw, -hl, o, 270.0, 360.0, chordal_tol)))
        chord_err = max(chord_err, chordal_error(o, 90.0,
                                                 max(arc_segments(o, 90.0, chordal_tol), 2)))

    z_apex = -leg / tool.tan_half  # virtual apex sits one vertical leg down
    simple = simple_construction(shaft, slot, tool, leg)

    path = ChamferPath(
        shaft=shaft, slot=slot, tool=tool, leg=leg, segments=segments, z_apex=z_apex,
        drop_max=drop_max, offset_at_flank=off_flank, simple=simple,
        chord_error=chord_err,
    )
    path.warnings = _warnings(path)
    return path


def simple_construction(shaft: Shaft, slot: Slot, tool: ChamferTool, leg: float) -> dict:
    """The by-hand version: widen the outline, keep the length and corner radius.

    Returns the four numbers you would type into OneCNC, plus the worst error
    that construction carries and where it occurs, found numerically rather
    than from the closed form so it stays honest for any angle or end style.
    """
    hw = slot.half_width
    tan = tool.tan_half
    off = shaft.drop(hw) * tan

    worst, worst_at = 0.0, 0.0
    if slot.end_style == "round":
        # The hand-built shape is the true outline translated sideways by `off`,
        # so along an end arc its useful (normal) component is off*cos(theta)
        # while the requirement is drop(x)*tan(a).
        for i in range(901):
            deg = i / 10.0
            th = math.radians(deg)
            x = hw * math.cos(th)
            err = off * math.cos(th) - shaft.drop(x) * tan
            if err > worst:
                worst, worst_at = err, deg
    elif slot.end_style == "square":
        for i in range(901):
            x = hw * i / 900.0
            err = off - shaft.drop(x) * tan
            if err > worst:
                worst, worst_at = err, x
    return {
        "length": slot.length,
        "width": slot.width + 2 * off,
        "corner_radius": hw if slot.end_style == "round" else 0.0,
        "offset": off,
        "max_error": worst,
        "max_error_at": worst_at,
        "error_units": "deg into each end arc" if slot.end_style == "round" else "mm from x=0",
        "error_fraction": worst / leg if leg else 0.0,
    }


def _warnings(p: ChamferPath) -> list[str]:
    out = []
    tool, slot, shaft = p.tool, p.slot, p.shaft

    frac = p.simple.get("error_fraction", 0.0)
    if p.simple.get("max_error", 0.0) > 0:
        if frac >= 0.25:
            out.append(
                f"the by-hand construction over-cuts by {p.simple['max_error']:.4f} mm, which "
                f"is {frac * 100:.0f}% of a {p.leg:g} mm chamfer - use the exact DXF path "
                f"instead, it costs nothing and removes this")
        else:
            out.append(
                f"the by-hand construction over-cuts by {p.simple['max_error']:.4f} mm "
                f"({frac * 100:.0f}% of the chamfer); acceptable for a deburr, worth avoiding "
                f"if the chamfer is cosmetic")

    if slot.width > 0.7 * shaft.diameter:
        out.append(
            f"a {slot.width:g} mm slot in a {shaft.diameter:g} mm shaft drops "
            f"{p.drop_max:.3f} mm across the edge; at this ratio the flank is steep enough "
            f"that a 2D chamfer will vary visibly - consider a 3D path or a form tool")
    if p.drop_max > 3 * p.leg:
        out.append(
            f"the {p.drop_max:.3f} mm edge drop is more than 3x the {p.leg:g} mm chamfer, so "
            f"the compensation dominates the cut; check the first part carefully")

    # Does the cone reach across and touch the far edge?
    far_height = -shaft.drop(slot.half_width) - p.z_apex
    if far_height > 0:
        cone_radius = far_height * tool.tan_half
        clearance = slot.width + p.offset_at_flank
        if cone_radius > clearance:
            out.append(
                f"at Z{p.z_apex:.3f} the cone is {cone_radius:.2f} mm across at the height of "
                f"the opposite edge but only {clearance:.2f} mm away from it - the tool will "
                f"gouge the far side")
    if tool.diameter and tool.diameter / 2.0 < p.leg:
        out.append(f"a {tool.diameter:g} mm tool cannot produce a {p.leg:g} mm leg before it "
                   f"runs out of flank")
    if tool.tip_diameter > 0:
        out.append(f"tool has a {tool.tip_diameter:g} mm flat, so the virtual point sits "
                   f"{tool.tip_rise:.4f} mm below it - program Z{p.z_flat:.4f} if the length "
                   f"offset is set on the flat, Z{p.z_apex:.4f} if it is set on the point")
    if slot.depth is not None and p.leg_vertical >= slot.depth:
        out.append(f"the chamfer reaches {p.leg_vertical:.3f} mm down but the slot is only "
                   f"{slot.depth:g} mm deep")
    return out


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def to_dxf(path: ChamferPath, include_reference: bool = True) -> Dxf:
    """DXF for import into OneCNC. The compensated path is on its own layer."""
    d = Dxf("mm")
    for kind, pts in path.segments:
        if kind == "line":
            d.line(pts[0][0], pts[0][1], pts[1][0], pts[1][1], "CHAMFER_PATH")
        else:
            d.polyline(pts, "CHAMFER_PATH")

    if include_reference:
        slot, hw = path.slot, path.slot.half_width
        # The true slot outline, for eyeballing the compensation. Switch this
        # layer off before selecting geometry for the toolpath.
        if slot.end_style == "round":
            c = slot.arc_center_y
            d.line(hw, -c, hw, c, "SLOT_NOMINAL")
            d.line(-hw, -c, -hw, c, "SLOT_NOMINAL")
            d.arc(0.0, c, hw, 0.0, 180.0, "SLOT_NOMINAL")
            d.arc(0.0, -c, hw, 180.0, 360.0, "SLOT_NOMINAL")
        elif slot.end_style == "square":
            hl = slot.length / 2.0
            d.line(hw, -hl, hw, hl, "SLOT_NOMINAL")
            d.line(-hw, hl, -hw, -hl, "SLOT_NOMINAL")
            d.line(hw, hl, -hw, hl, "SLOT_NOMINAL")
            d.line(-hw, -hl, hw, -hl, "SLOT_NOMINAL")
        d.circle(0.0, 0.0, path.shaft.radius, "SHAFT_OD")
        note = (f"shaft {path.shaft.diameter:g} slot {slot.width:g} "
                f"chamfer {path.leg:g} at Z{path.z_apex:.4f} "
                f"tool {path.tool.included_angle:g}deg")
        d.text(-path.shaft.radius, path.shaft.radius + 4.0, note, 2.0)
    return d


def report(path: ChamferPath) -> str:
    s, sl, t, simp = path.shaft, path.slot, path.tool, path.simple
    ends = {"round": f"round ends (R{sl.half_width:g})", "square": "square ends",
            "open": "open ended"}[sl.end_style]
    L = [
        f"CHAMFER PATH -- {sl.width:g} mm slot in a {s.diameter:g} mm shaft",
        "=" * 62,
        f"  shaft            {s.diameter:g} mm dia (R{s.radius:g})",
        f"  slot             {sl.width:g} mm wide"
        + (f" x {sl.length:g} mm long, {ends}" if sl.end_style != "open" else f", {ends}"),
        f"  tool             {t.included_angle:g} deg included "
        f"({math.degrees(t.half_angle):g} deg flank), {t.diameter:g} mm dia",
        f"  chamfer          {path.leg:g} mm radial x {path.leg_vertical:.4f} mm deep "
        f"({path.face_width:.4f} mm face)",
        "",
        "GEOMETRY",
        f"  edge drop        {path.drop_max:.4f} mm below the top of the shaft",
        f"                   sqrt({s.radius:g}^2 - {sl.half_width:g}^2) = "
        f"{math.sqrt(s.radius ** 2 - sl.half_width ** 2):.4f}",
        f"  program Z        {path.z_apex:.4f}  (virtual cone point, Z0 = shaft top)",
    ]
    if t.tip_diameter > 0:
        L.append(f"  or Z             {path.z_flat:.4f}  if the offset is set on the "
                 f"{t.tip_diameter:g} mm flat")
    L += [
        f"  offset at flanks {path.offset_at_flank:.4f} mm outward",
        f"  offset at apex   0.0000 mm  (the edge is back at full height there)",
        "",
    ]
    if sl.end_style != "open":
        L += [
            "BY HAND IN ONECNC  (what you are doing now)",
            f"  rounded rectangle  {simp['length']:g} x {simp['width']:.4f} mm"
            + (f", corner R{simp['corner_radius']:g}" if simp["corner_radius"] else ""),
            f"  run at             Z{path.z_apex:.4f}",
            f"  worst over-cut     {simp['max_error']:.4f} mm at "
            f"{simp['max_error_at']:.0f} {simp['error_units']}"
            f"  ({simp['error_fraction'] * 100:.0f}% of the chamfer)",
            "",
        ]
    L += [
        "EXACT PATH",
        f"  {len(path.points())} points, {path.path_length():.2f} mm long, "
        f"chordal error < {path.chord_error * 1000:.1f} um",
        "  offset varies as drop(x)*tan(a) instead of ramping linearly",
    ]
    worst_gap, closing = path.continuity()
    L.append(f"  entity handover gap {worst_gap:.6f} mm"
             + (f", loop closes to {closing:.6f} mm" if path.closed
                else " (two open passes, chain each separately)"))
    if path.warnings:
        L.append("")
        L.append("CHECK")
        for w in path.warnings:
            L.append(f"  ! {w}")
    return "\n".join(L)


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="chamfer",
        description="Generate a compensated 2D chamfer path for a lengthwise slot "
                    "in a round shaft.")
    ap.add_argument("-D", "--shaft", type=float, required=True, help="shaft diameter, mm")
    ap.add_argument("-w", "--slot-width", type=float, required=True, help="slot width, mm")
    ap.add_argument("-l", "--slot-length", type=float, default=0.0,
                    help="slot length, mm (omit for an open-ended slot)")
    ap.add_argument("-c", "--chamfer", type=float, default=0.4,
                    help="radial chamfer leg, mm (default 0.4)")
    ap.add_argument("-a", "--angle", type=float, default=90.0,
                    help="tool included angle, deg (default 90)")
    ap.add_argument("-e", "--ends", choices=END_STYLES, default="round")
    ap.add_argument("--tip-diameter", type=float, default=0.0,
                    help="flat at the tool point, mm")
    ap.add_argument("--tool-diameter", type=float, default=6.0)
    ap.add_argument("--slot-depth", type=float, help="only used for sanity checks")
    ap.add_argument("--tol", type=float, default=0.005, help="chordal tolerance, mm")
    ap.add_argument("--dxf", help="write the path to this DXF file")
    a = ap.parse_args(argv)

    path = build(
        Shaft(a.shaft),
        Slot(a.slot_width, a.slot_length, a.ends, a.slot_depth),
        ChamferTool(included_angle=a.angle, diameter=a.tool_diameter,
                    tip_diameter=a.tip_diameter),
        leg=a.chamfer, chordal_tol=a.tol,
    )
    print()
    print(report(path))
    if a.dxf:
        d = to_dxf(path)
        d.write(a.dxf)
        print(f"\n  DXF   {a.dxf}  ({d.entity_count} entities; path on layer "
              f"CHAMFER_PATH)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
