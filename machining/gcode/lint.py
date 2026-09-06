"""Safety and sanity rules for a posted milling program.

Written against the failure modes that actually bend tools and scrap parts on
a VMC, in rough order of how expensive the mistake is. Every rule states what
it saw and what to change, because a diagnostic you can't act on is noise.

Severities:
  crash  -- will very likely break something. Do not run.
  error  -- the control will alarm out, or the part will be wrong.
  warn   -- legal, but a good machinist would look twice.
  info   -- housekeeping.
"""
from __future__ import annotations

from dataclasses import dataclass

from .interp import Result, Move
from .machine import Setup
from .parser import Block

SEVERITY_ORDER = {"crash": 0, "error": 1, "warn": 2, "info": 3}


@dataclass
class Diagnostic:
    line_no: int
    rule: str
    severity: str
    message: str
    fix: str = ""

    def __str__(self) -> str:
        s = f"{self.severity.upper():<5} line {self.line_no:>5}  [{self.rule}] {self.message}"
        if self.fix:
            s += f"\n                       fix: {self.fix}"
        return s


def _seg_hits_box_xy(p0, p1, xmin, xmax, ymin, ymax) -> bool:
    """Slab test: does the XY segment p0->p1 touch the axis-aligned box?"""
    t0, t1 = 0.0, 1.0
    for i, (lo, hi) in enumerate(((xmin, xmax), (ymin, ymax))):
        d = p1[i] - p0[i]
        if abs(d) < 1e-12:
            if p0[i] < lo or p0[i] > hi:
                return False
            continue
        a, b = (lo - p0[i]) / d, (hi - p0[i]) / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return False
    return True


def _moved_xy(m: Move) -> bool:
    return abs(m.end[0] - m.start[0]) > 1e-6 or abs(m.end[1] - m.start[1]) > 1e-6


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------
def rule_rapid_into_stock(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    """The classic: G0 traversing across the part below the top of the stock."""
    out = []
    stock = setup.stock
    if stock is None:
        return out
    for m in res.moves:
        if m.kind not in ("rapid", "cycle_rapid") or m.machine_coords:
            continue
        lowest = min(m.start[2], m.end[2])
        if lowest >= stock.z_top - 1e-9:
            continue
        if _moved_xy(m) and _seg_hits_box_xy(m.start, m.end, stock.x_min, stock.x_max,
                                             stock.y_min, stock.y_max):
            out.append(Diagnostic(
                m.line_no, "RAPID_XY_IN_STOCK", "crash",
                f"G0 traverses X{m.start[0]:.3f} Y{m.start[1]:.3f} -> X{m.end[0]:.3f} "
                f"Y{m.end[1]:.3f} at Z{lowest:.3f}, which is {stock.z_top - lowest:.3f} mm "
                f"below the top of the stock and inside its footprint",
                "retract to the clearance plane (Z%.3f) before the traverse, or make this "
                "a feed move if it is meant to be cutting" % stock.safe_z))
        elif (not _moved_xy(m) and m.from_cycle is None
                and m.end[2] < m.start[2]
                and stock.is_inside_xy(m.end[0], m.end[1])
                and m.end[2] < stock.z_top - 1e-9):
            out.append(Diagnostic(
                m.line_no, "RAPID_PLUNGE_INTO_STOCK", "crash",
                f"G0 plunges to Z{m.end[2]:.3f}, below the stock top (Z{stock.z_top:.3f}), "
                f"at X{m.end[0]:.3f} Y{m.end[1]:.3f}",
                "rapid only down to the clearance/R plane, then feed (G1) the rest"))
    return out


def rule_cut_below_stock(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    """Cutting past the bottom of the stock means cutting the vise or table."""
    out = []
    stock = setup.stock
    if stock is None:
        return out
    worst: dict[int, float] = {}
    for m in res.moves:
        if m.machine_coords or m.kind == "dwell":
            continue
        z = m.end[2]
        if z < stock.z_bottom - 1e-6 and stock.is_inside_xy(m.end[0], m.end[1]):
            worst[m.line_no] = min(worst.get(m.line_no, 0.0), z)
    for line_no, z in sorted(worst.items()):
        out.append(Diagnostic(
            line_no, "CUT_BELOW_STOCK", "crash",
            f"tool reaches Z{z:.3f}, which is {stock.z_bottom - z:.3f} mm below the bottom "
            f"of the stock (Z{stock.z_bottom:.3f})",
            "check the stock height and the part Z datum; if this is a through feature, "
            "model the breakthrough allowance into the stock definition"))
    return out


def rule_tool_length_comp(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    """A Z move after a tool change with no G43 active runs on the last tool's offset."""
    out = []
    flagged: set[int | None] = set()
    for m in res.moves:
        if m.machine_coords or abs(m.end[2] - m.start[2]) < 1e-9:
            continue
        if m.h_offset is None and m.tool not in flagged:
            flagged.add(m.tool)
            out.append(Diagnostic(
                m.line_no, "NO_TOOL_LENGTH_COMP", "crash",
                f"Z motion to {m.end[2]:.3f} with no G43 tool length offset active"
                + (f" (tool T{m.tool})" if m.tool else ""),
                "add `G43 H%s Z...` on the first Z approach after the tool change"
                % (m.tool if m.tool else "#")))
    return out


def rule_h_matches_t(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    out = []
    seen = set()
    for m in res.moves:
        if m.tool is None or m.h_offset is None or m.tool in seen:
            continue
        seen.add(m.tool)
        expected = setup.tools[m.tool].expected_h if m.tool in setup.tools else m.tool
        if m.h_offset != expected:
            out.append(Diagnostic(
                m.line_no, "H_TOOL_MISMATCH", "crash",
                f"T{m.tool} is running on length offset H{m.h_offset}, expected H{expected}",
                f"change to `G43 H{expected}`, or set length_offset on the tool in the setup "
                f"if this machine genuinely maps them differently"))
    return out


def rule_work_offset(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    out = []
    for m in res.moves:
        if m.machine_coords:
            continue
        if m.wcs is None:
            out.append(Diagnostic(
                m.line_no, "NO_WORK_OFFSET", "crash",
                "motion commanded before any work offset (G54-G59) is selected; the control "
                "will use whatever offset was left active",
                f"add `{setup.expected_wcs[0]}` to the first positioning block"))
            break
        if m.wcs not in setup.expected_wcs:
            out.append(Diagnostic(
                m.line_no, "UNEXPECTED_WORK_OFFSET", "error",
                f"program uses {m.wcs} but the setup is dialled in on "
                f"{', '.join(setup.expected_wcs)}",
                "re-post against the correct offset, or update the setup"))
            break
    return out


def rule_spindle(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    out = []
    for m in res.moves:
        if m.kind in ("rapid", "cycle_rapid", "dwell") or m.machine_coords:
            continue
        if not m.spindle_on:
            out.append(Diagnostic(
                m.line_no, "CUT_WITH_SPINDLE_OFF", "crash",
                "cutting move with the spindle stopped",
                "add M3 (with an S word) before the first cutting move"))
            break
        if not m.spindle_rpm:
            out.append(Diagnostic(
                m.line_no, "NO_SPINDLE_SPEED", "error",
                "spindle started but no S speed was ever commanded", "add an S word with the M3"))
            break
    return out


def rule_limits(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    out = []
    mach = setup.machine
    worst_s = max((m.spindle_rpm or 0) for m in res.moves) if res.moves else 0
    if worst_s > mach.max_rpm:
        line = next(m.line_no for m in res.moves if (m.spindle_rpm or 0) == worst_s)
        out.append(Diagnostic(
            line, "OVER_MAX_RPM", "error",
            f"S{worst_s:g} exceeds the {mach.name} maximum of {mach.max_rpm:g} rpm",
            f"clamp the speed to {mach.max_rpm:g} and recompute the feed to hold chip load"))
    feeds = [(m.feed, m.line_no) for m in res.moves if m.feed]
    if feeds:
        f, line = max(feeds)
        if f > mach.max_feed_mm_min:
            out.append(Diagnostic(
                line, "OVER_MAX_FEED", "error",
                f"F{f:.1f} mm/min exceeds the machine maximum of {mach.max_feed_mm_min:g}",
                "the control will clamp this silently; re-post with a feed the machine can hold"))
    return out


def rule_envelope(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    """Only checkable when the caller supplies the actual WCS offset."""
    out = []
    off = getattr(setup, "wcs_offset", None)
    if off is None:
        return out
    env = setup.machine.envelope
    for m in res.moves:
        if m.machine_coords:
            continue
        mx, my, mz = (m.end[0] + off[0], m.end[1] + off[1], m.end[2] + off[2])
        if not env.contains(mx, my, mz, tol=0.001):
            out.append(Diagnostic(
                m.line_no, "OUTSIDE_ENVELOPE", "error",
                f"machine position X{mx:.2f} Y{my:.2f} Z{mz:.2f} is outside the travel limits",
                "move the part datum on the table, or split the job into two setups"))
            break
    return out


def rule_modal_hygiene(blocks: list[Block], res: Result, setup: Setup) -> list[Diagnostic]:
    """State left dangling at a tool change or at program end."""
    out = []
    st = res.end_state
    last = max((b.line_no for b in blocks if not b.is_empty), default=1)
    if st is None:
        return out
    if st.cutter_comp != "off":
        out.append(Diagnostic(last, "COMP_LEFT_ON", "error",
                              f"cutter compensation ({st.cutter_comp}) is still active at the "
                              f"end of the program",
                              "cancel with G40 before the final retract"))
    if st.canned is not None:
        out.append(Diagnostic(last, "CYCLE_LEFT_ON", "error",
                              f"canned cycle G{st.canned:g} was never cancelled",
                              "add G80 after the last hole"))
    if st.incremental:
        out.append(Diagnostic(last, "INCREMENTAL_LEFT_ON", "warn",
                              "program ends in G91 incremental mode",
                              "restore G90 so the next program starts predictably"))
    if st.coolant:
        out.append(Diagnostic(last, "COOLANT_LEFT_ON", "warn",
                              "coolant is still on at program end", "add M9 before M30"))
    if st.spindle_on:
        out.append(Diagnostic(last, "SPINDLE_LEFT_ON", "warn",
                              "spindle is still running at program end", "add M5 before M30"))

    # Tool changes must happen with the cycle cancelled and the tool clear.
    for tc in res.tool_changes:
        prior = [m for m in res.moves if m.line_no < tc.line_no]
        if prior and not any(m.machine_coords for m in prior[-4:]):
            z = prior[-1].end[2]
            if setup.stock and z < setup.stock.safe_z:
                out.append(Diagnostic(
                    tc.line_no, "TOOL_CHANGE_NOT_CLEAR", "warn",
                    f"T{tc.tool} M6 with the previous tool at Z{z:.3f}, below the clearance "
                    f"plane (Z{setup.stock.safe_z:.3f})",
                    "add `G91 G28 Z0.` or `G53 G0 Z0.` before the tool change"))
    return out


def rule_safe_start(blocks: list[Block], res: Result, setup: Setup) -> list[Diagnostic]:
    """Every hand-checked program opens with a known-state safety line."""
    head = [b for b in blocks[:12] if not b.is_empty]
    gs = {g for b in head for g in b.g_codes()}
    missing = [f"G{c:g}" for c in (17.0, 40.0, 49.0, 80.0, 90.0) if c not in gs]
    if missing:
        line = head[0].line_no if head else 1
        return [Diagnostic(
            line, "NO_SAFE_START", "warn",
            f"no safe-start line; missing {', '.join(missing)} in the program header",
            "open with `G17 G20/G21 G40 G49 G80 G90` so the program does not inherit "
            "modal state from whatever ran last")]
    return []


def rule_program_end(blocks: list[Block], res: Result, setup: Setup) -> list[Diagnostic]:
    live = [b for b in blocks if not b.is_empty]
    if not any(b.has_m(30.0, 2.0) for b in live):
        return [Diagnostic(live[-1].line_no if live else 1, "NO_PROGRAM_END", "error",
                           "program has no M30 or M02",
                           "end with M30 so the control rewinds for the next part")]
    return []


def rule_tools_known(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    out = []
    if not setup.tools:
        return out
    for tc in res.tool_changes:
        if tc.tool not in setup.tools:
            out.append(Diagnostic(tc.line_no, "TOOL_NOT_IN_SETUP", "error",
                                  f"T{tc.tool} is called but is not in the tool list",
                                  "add it to the setup, or fix the tool number in the post"))
        elif tc.tool > setup.machine.tool_capacity:
            out.append(Diagnostic(tc.line_no, "TOOL_OVER_CAPACITY", "error",
                                  f"T{tc.tool} exceeds the {setup.machine.tool_capacity}-pocket "
                                  f"carousel", "renumber the tool"))
    return out


def rule_unsupported_codes(blocks: list[Block], res: Result, setup: Setup) -> list[Diagnostic]:
    out = []
    mach = setup.machine
    for b in blocks:
        for g in b.g_codes():
            if g in mach.unsupported_g:
                out.append(Diagnostic(b.line_no, "UNSUPPORTED_G", "error",
                                      f"G{g:g} is not supported on the {mach.control} control",
                                      "check the post configuration"))
        for m in b.m_codes():
            if m in mach.unsupported_m:
                out.append(Diagnostic(b.line_no, "UNSUPPORTED_M", "error",
                                      f"M{m:g} is not supported on the {mach.control} control",
                                      "check the post configuration"))
    return out


def rule_interp_problems(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    sev = {"ARC_RADIUS_MISMATCH": "error", "ARC_R_IMPOSSIBLE": "error",
           "ARC_NO_CENTER": "error", "NO_FEED": "crash",
           "MACRO_UNSIMULATED": "warn", "NO_RIGID_TAP": "error"}
    fixes = {
        "ARC_RADIUS_MISMATCH": "the post is emitting inconsistent I/J; tighten the arc "
                               "tolerance in OneCNC or output arcs as lines",
        "NO_FEED": "add an F word before the first G1",
        "MACRO_UNSIMULATED": "verify this section by hand or single-block it on the machine",
    }
    return [Diagnostic(p.line_no, p.code, sev.get(p.code, "warn"), p.message,
                       fixes.get(p.code, "")) for p in res.problems]


def rule_plunge_feed(blocks, res: Result, setup: Setup) -> list[Diagnostic]:
    """Straight-down plunges at the lateral feedrate chip-load the end of the tool."""
    out = []
    reported = 0
    for m in res.moves:
        if m.kind != "feed" or m.machine_coords or not m.feed:
            continue
        dz = m.end[2] - m.start[2]
        if dz >= -1e-6 or _moved_xy(m):
            continue
        if setup.stock and m.start[2] <= setup.stock.z_top and m.feed > 400:
            out.append(Diagnostic(
                m.line_no, "FAST_PLUNGE", "warn",
                f"straight-down plunge of {-dz:.2f} mm at F{m.feed:.0f}; the centre of an "
                f"end mill cuts at zero surface speed",
                "ramp or helix into the cut, or drop the plunge feed to roughly a third "
                "of the lateral feed"))
            reported += 1
            if reported >= 3:
                break
    return out


ALL_RULES = [
    rule_rapid_into_stock, rule_cut_below_stock, rule_tool_length_comp,
    rule_h_matches_t, rule_work_offset, rule_spindle, rule_limits, rule_envelope,
    rule_modal_hygiene, rule_safe_start, rule_program_end, rule_tools_known,
    rule_unsupported_codes, rule_interp_problems, rule_plunge_feed,
]


def lint(blocks: list[Block], res: Result, setup: Setup) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for rule in ALL_RULES:
        out.extend(rule(blocks, res, setup))
    out.sort(key=lambda d: (SEVERITY_ORDER.get(d.severity, 9), d.line_no))
    return out
