"""Interpreter / backplotter for Fanuc-Haas milling programs.

Walks the blocks from parser.py maintaining full modal state, and emits a flat
list of `Move` records in millimetres, work coordinates. Canned cycles are
expanded into the moves the control would actually make, so cycle-time and
depth checks see real motion rather than a single cryptic G83 block.

Scope is 3-axis milling with the common canned cycles. It does not attempt
macro-B (#vars, IF/GOTO), subprogram call trees deeper than M98/M99, or
4th/5th axis kinematics -- those are reported as unsupported rather than
silently mis-simulated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .machine import MM_PER_INCH, Setup
from .parser import Block

# --- modal group membership -------------------------------------------------
MOTION_CODES = {0.0, 1.0, 2.0, 3.0}
CANNED_CODES = {73.0, 74.0, 76.0, 81.0, 82.0, 83.0, 84.0, 85.0, 86.0, 87.0, 88.0, 89.0}
PLANE_CODES = {17.0: "XY", 18.0: "ZX", 19.0: "YZ"}
WCS_CODES = {54.0: "G54", 55.0: "G55", 56.0: "G56", 57.0: "G57", 58.0: "G58", 59.0: "G59"}
# Macro / flow-control words we cannot faithfully simulate.
UNSIMULATED = {65.0, 66.0, 67.0}


@dataclass
class Move:
    """One motion segment in mm, work coordinates."""

    line_no: int
    kind: str  # rapid | feed | arc_cw | arc_ccw | dwell | cycle_rapid | cycle_feed
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    feed: float | None  # mm/min, as commanded
    seconds: float
    length: float  # mm along the path
    tool: int | None
    h_offset: int | None
    wcs: str | None
    spindle_rpm: float | None
    spindle_on: bool
    coolant: bool
    cutter_comp: str  # 'off' | 'left' | 'right'
    center: tuple[float, float, float] | None = None
    plane: str = "XY"
    machine_coords: bool = False  # G53 / G28 motion
    from_cycle: str | None = None  # e.g. 'G83'
    note: str = ""  # interpreter complaint attached to this move


@dataclass
class ToolChange:
    line_no: int
    tool: int
    seconds: float


@dataclass
class Problem:
    """An interpreter-level fault (not a style rule -- see lint.py for those)."""

    line_no: int
    code: str
    message: str


@dataclass
class Result:
    moves: list[Move] = field(default_factory=list)
    tool_changes: list[ToolChange] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    tools_used: list[int] = field(default_factory=list)
    units: str = "mm"
    end_state: "State | None" = None

    @property
    def cycle_seconds(self) -> float:
        return sum(m.seconds for m in self.moves) + sum(t.seconds for t in self.tool_changes)

    @property
    def cut_distance(self) -> float:
        return sum(m.length for m in self.moves if m.kind not in ("rapid", "cycle_rapid", "dwell"))

    @property
    def rapid_distance(self) -> float:
        return sum(m.length for m in self.moves if m.kind in ("rapid", "cycle_rapid"))

    def bounds(self) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        pts = [p for m in self.moves for p in (m.start, m.end) if not m.machine_coords]
        if not pts:
            return None
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi  # type: ignore[return-value]


@dataclass
class State:
    """Modal state of the control."""

    motion: float = 0.0
    canned: float | None = None
    plane: str = "XY"
    inch: bool = False
    incremental: bool = False
    feed_mode: float = 94.0
    wcs: str | None = None
    tool: int | None = None
    h_offset: int | None = None
    tool_len_comp: bool = False
    cutter_comp: str = "off"
    d_offset: int | None = None
    spindle_rpm: float | None = None
    spindle_on: bool = False
    coolant: bool = False
    feed: float | None = None  # mm/min
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    retract_mode: float = 98.0  # G98 initial-Z / G99 R-plane
    # canned-cycle parameters
    cyc_z: float | None = None
    cyc_r: float | None = None
    cyc_q: float | None = None
    cyc_p: float = 0.0
    cyc_initial_z: float = 0.0

    @property
    def pos(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


def _scale(v: float, inch: bool) -> float:
    return v * MM_PER_INCH if inch else v


class Interpreter:
    def __init__(self, setup: Setup, honour_block_delete: bool = False):
        self.setup = setup
        self.machine = setup.machine
        # Block-delete OFF (the default) means slashed blocks DO run, which is
        # the conservative assumption for crash checking.
        self.honour_block_delete = honour_block_delete
        self.st = State()
        self.res = Result()

    # -- helpers ------------------------------------------------------------
    def _problem(self, line_no: int, code: str, message: str) -> None:
        self.res.problems.append(Problem(line_no, code, message))

    def _rapid_rate(self) -> float:
        return self.machine.rapid_mm_min

    def _target(self, blk: Block) -> tuple[float, float, float]:
        st = self.st
        out = []
        for letter, cur in (("X", st.x), ("Y", st.y), ("Z", st.z)):
            v = blk.get(letter)
            if v is None:
                out.append(cur)
            else:
                v = _scale(v, st.inch)
                out.append(cur + v if st.incremental else v)
        return (out[0], out[1], out[2])

    def _emit(self, line_no: int, kind: str, end, *, feed=None, length=None,
              center=None, seconds=None, machine_coords=False, from_cycle=None,
              note="") -> None:
        st = self.st
        start = st.pos
        if length is None:
            length = math.dist(start, end)
        if seconds is None:
            if kind in ("rapid", "cycle_rapid"):
                rate = self._rapid_rate()
            else:
                rate = feed if feed and feed > 0 else None
            seconds = (length / rate * 60.0) if rate else 0.0
        self.res.moves.append(
            Move(
                line_no=line_no, kind=kind, start=start, end=tuple(end), feed=feed,
                seconds=seconds, length=length, tool=st.tool, h_offset=st.h_offset,
                wcs=st.wcs, spindle_rpm=st.spindle_rpm, spindle_on=st.spindle_on,
                coolant=st.coolant, cutter_comp=st.cutter_comp, center=center,
                plane=st.plane, machine_coords=machine_coords, from_cycle=from_cycle,
                note=note,
            )
        )
        if not machine_coords:
            st.x, st.y, st.z = end

    def _effective_feed(self, blk: Block) -> float | None:
        st = self.st
        if st.feed_mode == 95.0 and st.feed is not None and st.spindle_rpm:
            return st.feed * st.spindle_rpm  # units/rev -> units/min
        return st.feed

    # -- arcs ---------------------------------------------------------------
    def _arc(self, blk: Block, end, cw: bool) -> None:
        st = self.st
        start = st.pos
        # Axis indices for the active plane: (a, b) in plane, c normal.
        ai, bi, ci = {"XY": (0, 1, 2), "ZX": (2, 0, 1), "YZ": (1, 2, 0)}[st.plane]
        offs_letters = {"XY": ("I", "J"), "ZX": ("K", "I"), "YZ": ("J", "K")}[st.plane]

        note = ""
        r_word = blk.get("R")
        center = None
        if r_word is not None:
            r = _scale(r_word, st.inch)
            center = self._center_from_r(start, end, r, ai, bi, ci, cw)
            if center is None:
                note = "R-form arc has no solution (endpoints further apart than 2R)"
                self._problem(blk.line_no, "ARC_R_IMPOSSIBLE", note)
                self._emit(blk.line_no, "feed", end, feed=self._effective_feed(blk), note=note)
                return
        else:
            oa = blk.get(offs_letters[0])
            ob = blk.get(offs_letters[1])
            if oa is None and ob is None:
                note = "arc with neither I/J/K offsets nor R"
                self._problem(blk.line_no, "ARC_NO_CENTER", note)
                self._emit(blk.line_no, "feed", end, feed=self._effective_feed(blk), note=note)
                return
            c = list(start)
            c[ai] = start[ai] + _scale(oa or 0.0, st.inch)
            c[bi] = start[bi] + _scale(ob or 0.0, st.inch)
            center = tuple(c)

        r_start = math.hypot(start[ai] - center[ai], start[bi] - center[bi])
        r_end = math.hypot(end[ai] - center[ai], end[bi] - center[bi])
        if abs(r_start - r_end) > 0.01:  # 10 micron: tighter than any real post
            note = (f"arc start/end radii disagree by {abs(r_start - r_end):.4f} mm "
                    f"({r_start:.4f} vs {r_end:.4f}) - control will alarm")
            self._problem(blk.line_no, "ARC_RADIUS_MISMATCH", note)

        a0 = math.atan2(start[bi] - center[bi], start[ai] - center[ai])
        a1 = math.atan2(end[bi] - center[bi], end[ai] - center[ai])
        sweep = a1 - a0
        if cw:
            while sweep >= 0:
                sweep -= 2 * math.pi
        else:
            while sweep <= 0:
                sweep += 2 * math.pi
        # Coincident endpoints with I/J means a full circle, not a zero move.
        if r_word is None and math.dist(start, end) < 1e-9:
            sweep = -2 * math.pi if cw else 2 * math.pi

        dc = end[ci] - start[ci]
        arc_len = math.hypot(r_start * sweep, dc)
        self._emit(blk.line_no, "arc_cw" if cw else "arc_ccw", end,
                   feed=self._effective_feed(blk), length=arc_len, center=center, note=note)

    @staticmethod
    def _center_from_r(start, end, r, ai, bi, ci, cw):
        """Fanuc R-form: |R| picks the minor arc, negative R the major arc."""
        dx = end[ai] - start[ai]
        dy = end[bi] - start[bi]
        chord = math.hypot(dx, dy)
        if chord < 1e-9 or chord > 2 * abs(r) + 1e-6:
            return None
        h = math.sqrt(max(abs(r) ** 2 - (chord / 2) ** 2, 0.0))
        mx = (start[ai] + end[ai]) / 2
        my = (start[bi] + end[bi]) / 2
        ux, uy = -dy / chord, dx / chord
        # Sign selects which side of the chord the centre falls on.
        minor = r > 0
        sign = 1.0 if (cw != minor) else -1.0
        c = list(start)
        c[ai] = mx + sign * h * ux
        c[bi] = my + sign * h * uy
        c[ci] = start[ci]
        return tuple(c)

    # -- canned cycles ------------------------------------------------------
    def _canned_hole(self, blk: Block, x: float, y: float) -> None:
        st = self.st
        cyc = st.canned
        z_bot = st.cyc_z if st.cyc_z is not None else st.z
        r_plane = st.cyc_r if st.cyc_r is not None else st.z
        feed = self._effective_feed(blk) or 0.0
        tag = f"G{cyc:g}"

        # Rapid across at whatever height we're at, then down to R.
        self._emit(blk.line_no, "cycle_rapid", (x, y, st.z), from_cycle=tag)
        self._emit(blk.line_no, "cycle_rapid", (x, y, r_plane), from_cycle=tag)

        if cyc in (83.0, 73.0) and st.cyc_q and st.cyc_q > 0:
            q = st.cyc_q
            depth = r_plane
            while depth - z_bot > 1e-9:
                depth = max(depth - q, z_bot)
                self._emit(blk.line_no, "cycle_feed", (x, y, depth), feed=feed, from_cycle=tag)
                if depth - z_bot <= 1e-9:
                    break
                if cyc == 83.0:  # full retract to clear chips
                    self._emit(blk.line_no, "cycle_rapid", (x, y, r_plane), from_cycle=tag)
                    self._emit(blk.line_no, "cycle_rapid", (x, y, depth + 0.5), from_cycle=tag)
                else:  # G73 chip-break: small hop, stays in the hole
                    self._emit(blk.line_no, "cycle_rapid", (x, y, depth + 0.5), from_cycle=tag)
        else:
            self._emit(blk.line_no, "cycle_feed", (x, y, z_bot), feed=feed, from_cycle=tag)

        if st.cyc_p:
            self.res.moves.append(
                Move(blk.line_no, "dwell", (x, y, z_bot), (x, y, z_bot), None,
                     st.cyc_p, 0.0, st.tool, st.h_offset, st.wcs, st.spindle_rpm,
                     st.spindle_on, st.coolant, st.cutter_comp, from_cycle=tag)
            )

        if cyc in (85.0, 86.0, 89.0):  # bore cycles feed back out
            self._emit(blk.line_no, "cycle_feed", (x, y, r_plane), feed=feed, from_cycle=tag)
        else:
            self._emit(blk.line_no, "cycle_rapid", (x, y, r_plane), from_cycle=tag)

        if st.retract_mode == 98.0 and st.cyc_initial_z > r_plane:
            self._emit(blk.line_no, "cycle_rapid", (x, y, st.cyc_initial_z), from_cycle=tag)

    # -- main loop ----------------------------------------------------------
    def run(self, blocks: list[Block]) -> Result:
        st = self.st
        for blk in blocks:
            if blk.is_empty:
                continue
            if blk.block_delete and self.honour_block_delete:
                continue
            self._block(blk)
        self.res.units = "inch" if st.inch else "mm"
        self.res.end_state = st
        return self.res

    def _block(self, blk: Block) -> None:
        st = self.st
        gs = blk.g_codes()
        ms = blk.m_codes()

        for g in gs:
            if g in UNSIMULATED:
                self._problem(blk.line_no, "MACRO_UNSIMULATED",
                              f"G{g:g} (macro call) cannot be simulated; motion after this "
                              f"point may be wrong")

        # --- non-motion modals, applied before the move ---
        for g in gs:
            if g == 20.0:
                st.inch = True
            elif g == 21.0:
                st.inch = False
            elif g in PLANE_CODES:
                st.plane = PLANE_CODES[g]
            elif g == 90.0:
                st.incremental = False
            elif g == 91.0:
                st.incremental = True
            elif g in WCS_CODES:
                st.wcs = WCS_CODES[g]
            elif g in (93.0, 94.0, 95.0):
                st.feed_mode = g
            elif g == 40.0:
                st.cutter_comp, st.d_offset = "off", None
            elif g == 41.0:
                st.cutter_comp = "left"
                st.d_offset = int(blk.get("D")) if blk.has("D") else st.d_offset
            elif g == 42.0:
                st.cutter_comp = "right"
                st.d_offset = int(blk.get("D")) if blk.has("D") else st.d_offset
            elif g == 43.0:
                st.tool_len_comp = True
                if blk.has("H"):
                    st.h_offset = int(blk.get("H"))
            elif g == 49.0:
                st.tool_len_comp, st.h_offset = False, None
            elif g in (98.0, 99.0):
                st.retract_mode = g
            elif g == 80.0:
                st.canned = None

        if blk.has("F"):
            st.feed = _scale(blk.get("F"), st.inch)
        if blk.has("S"):
            st.spindle_rpm = blk.get("S")

        for m in ms:
            if m == 3.0 or m == 4.0:
                st.spindle_on = True
            elif m == 5.0:
                st.spindle_on = False
            elif m in (7.0, 8.0):
                st.coolant = True
            elif m == 9.0:
                st.coolant = False
            elif m == 6.0:
                t = blk.get("T")
                tool = int(t) if t is not None else st.tool
                if tool is not None:
                    st.tool = tool
                    if tool not in self.res.tools_used:
                        self.res.tools_used.append(tool)
                    self.res.tool_changes.append(
                        ToolChange(blk.line_no, tool, self.machine.tool_change_seconds)
                    )
                st.tool_len_comp, st.h_offset = False, None
                st.canned = None

        # --- dwell ---
        if any(g == 4.0 for g in gs):
            p = blk.get("P")
            secs = (p / 1000.0 if p and p > 1 and float(p).is_integer() else (p or 0.0))
            if blk.has("X"):
                secs = blk.get("X")
            self.res.moves.append(
                Move(blk.line_no, "dwell", st.pos, st.pos, None, secs, 0.0, st.tool,
                     st.h_offset, st.wcs, st.spindle_rpm, st.spindle_on, st.coolant,
                     st.cutter_comp)
            )
            return

        # --- machine-coordinate and reference moves ---
        if any(g == 53.0 for g in gs):
            end = self._target(blk)
            self._emit(blk.line_no, "rapid", end, machine_coords=True)
            return
        if any(g in (28.0, 30.0) for g in gs):
            inter = self._target(blk)
            self._emit(blk.line_no, "rapid", inter)
            self._emit(blk.line_no, "rapid", (inter[0], inter[1], 0.0),
                       machine_coords=True, note="return to machine reference")
            st.z = max(st.z, 0.0)
            st.canned = None
            return

        # --- canned cycle setup ---
        cyc = next((g for g in gs if g in CANNED_CODES), None)
        if cyc is not None:
            if st.canned is None:
                st.cyc_initial_z = st.z
            st.canned = cyc
            if blk.has("Z"):
                z = _scale(blk.get("Z"), st.inch)
                st.cyc_z = st.z + z if st.incremental else z
            if blk.has("R"):
                r = _scale(blk.get("R"), st.inch)
                st.cyc_r = st.z + r if st.incremental else r
            if blk.has("Q"):
                st.cyc_q = abs(_scale(blk.get("Q"), st.inch))
            if blk.has("P"):
                st.cyc_p = blk.get("P") / 1000.0
            if cyc in (84.0, 74.0) and not self.machine.has_rigid_tapping:
                self._problem(blk.line_no, "NO_RIGID_TAP",
                              f"G{cyc:g} tapping cycle but machine profile has no rigid tapping")

        # --- motion ---
        motion = next((g for g in gs if g in MOTION_CODES), None)
        if motion is not None:
            st.motion = motion
            st.canned = None

        has_axis = blk.has("X") or blk.has("Y") or blk.has("Z")

        if st.canned is not None:
            if has_axis and (blk.has("X") or blk.has("Y")):
                tx, ty, _ = self._target(blk)
                reps = int(blk.get("L") or blk.get("K") or 1)
                for _ in range(max(reps, 1)):
                    self._canned_hole(blk, tx, ty)
            elif cyc is not None:
                # Cycle defined on a block with only Z/R: drill at current XY.
                self._canned_hole(blk, st.x, st.y)
            return

        if not has_axis:
            return

        end = self._target(blk)
        if st.motion == 0.0:
            self._emit(blk.line_no, "rapid", end)
        elif st.motion == 1.0:
            feed = self._effective_feed(blk)
            if not feed:
                self._problem(blk.line_no, "NO_FEED", "G1 with no feedrate ever commanded")
            self._emit(blk.line_no, "feed", end, feed=feed)
        elif st.motion in (2.0, 3.0):
            self._arc(blk, end, cw=(st.motion == 2.0))


def run(blocks: list[Block], setup: Setup, honour_block_delete: bool = False) -> Result:
    return Interpreter(setup, honour_block_delete).run(blocks)
