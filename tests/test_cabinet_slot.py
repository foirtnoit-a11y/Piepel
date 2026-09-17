"""Tests for the cabinet-front notch generator.

The load-bearing ones are `test_mouth_span_matches_the_drawing` (the reading of
the drawing is arithmetically checkable, so check it) and
`test_program_is_silent` (a program this repo generates had better survive the
verifier this repo ships).
"""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "machining"))

import cabinet_slot as cs  # noqa: E402
import feeds  # noqa: E402
from gcode import parse, backplot, lint, Machine, Setup, Stock, Tool  # noqa: E402

TOOL_R = 4.0  # the 8 mm cutter the program is written for
LEAD = 2.0


def build(**kw):
    """Notch, panel, plan and machine with the drawing's numbers."""
    notch = cs.Notch(**{k: v for k, v in kw.items() if k in
                        ("mouth_width", "floor_width", "depth", "corner_radius")})
    panel = cs.Panel()
    plan = cs.CutPlan(rpm=17900.0, feed=2400.0)
    machine = Machine.load("stepcraft_m1000")
    return notch, panel, plan, machine


def setup_from(notch, panel, plan) -> Setup:
    d = cs.setup_dict(notch, panel, plan)
    return Setup(
        machine=Machine.load(d["machine"]),
        stock=Stock(**d["stock"]),
        tools={t["number"]: Tool(**t) for t in d["tools"]},
        expected_wcs=tuple(d["expected_wcs"]),
    )


class TestDrawing(unittest.TestCase):
    def test_mouth_span_matches_the_drawing(self):
        """65 + 2*R*tan(phi/2) has to come out at the 71.65 that was dimensioned.

        This is the whole justification for reading the drawing as a notch with
        R6 at the mouth rather than, say, a 3 mm deep face recess. If this ever
        stops holding, the geometry has been misread and nothing below matters.
        """
        n = cs.Notch()
        self.assertAlmostEqual(n.mouth_span, 71.65, places=2)
        self.assertAlmostEqual(n.floor_span, 33.35, places=2)

    def test_flank_angle_and_tangent(self):
        n = cs.Notch()
        self.assertAlmostEqual(math.degrees(n.flank_angle), 57.9946, places=3)
        self.assertAlmostEqual(n.tangent, n.corner_radius * math.tan(n.flank_angle / 2))
        # Both fillets back off their sharp corner by the same amount.
        self.assertAlmostEqual(n.mouth_span - n.mouth_width,
                               n.floor_width - n.floor_span)

    def test_rejects_geometry_no_cutter_can_make(self):
        with self.assertRaises(ValueError):
            cs.Notch(mouth_width=40.0, floor_width=65.0)   # undercut
        with self.assertRaises(ValueError):
            cs.Notch(corner_radius=25.0)                   # fillets overlap
        with self.assertRaises(ValueError):
            cs.Notch(depth=0.0)


class TestToolpath(unittest.TestCase):
    def setUp(self):
        self.n = cs.Notch()
        self.path = self.n.toolpath(TOOL_R, LEAD)

    def test_chain_is_continuous(self):
        """Every segment starts exactly where the last one ended."""
        worst = 0.0
        for a, b in zip(self.path, self.path[1:]):
            worst = max(worst, math.dist(a.p1, b.p0))
        self.assertLess(worst, 1e-9, f"worst handover gap {worst:.6f} mm")

    def test_arc_endpoints_agree_on_the_radius(self):
        """An arc whose endpoints disagree with I/J alarms the control."""
        for seg in self.path:
            if isinstance(seg, cs.Arc):
                self.assertLess(seg.radius_error, 1e-9)

    def test_arc_radii_and_senses(self):
        arcs = [s for s in self.path if isinstance(s, cs.Arc)]
        self.assertEqual(len(arcs), 4)
        R, r = self.n.corner_radius, TOOL_R
        # Mouth corners are convex material, so the tool swings round the
        # outside: R + r. Floor corners are inside corners: R - r.
        self.assertAlmostEqual(arcs[0].radius, R + r)
        self.assertAlmostEqual(arcs[3].radius, R + r)
        self.assertAlmostEqual(arcs[1].radius, R - r)
        self.assertAlmostEqual(arcs[2].radius, R - r)
        self.assertEqual([a.ccw for a in arcs], [True, False, False, True])
        for a in arcs:
            self.assertAlmostEqual(abs(a.sweep), self.n.flank_angle, places=9)

    def test_offset_is_exactly_the_tool_radius_from_the_part(self):
        """Sample the path against the part outline it is supposed to cut."""
        n, r = self.n, TOOL_R
        a, b, h, t = n.half_mouth, n.half_floor, n.depth, n.tangent

        # The floor of the notch is y = h; the tool centre runs r short of it.
        floor = self.path[4]
        self.assertAlmostEqual(floor.p0[1], h - r)
        self.assertAlmostEqual(floor.p1[1], h - r)

        # The left flank of the part runs (-a, 0) -> (-b, h). Both ends of the
        # programmed flank must sit exactly r away from that line.
        p0, p1 = (-a, 0.0), (-b, h)
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        flank = self.path[2]
        for p in (flank.p0, flank.p1):
            # Perpendicular distance from the infinite flank line.
            dist = abs(dx * (p[1] - p0[1]) - dy * (p[0] - p0[0])) / L
            self.assertAlmostEqual(dist, r, places=9)

        # Fillet centres are the part's, not the path's: only the radius moves.
        self.assertAlmostEqual(self.path[1].center[0], -(a + t))
        self.assertAlmostEqual(self.path[1].center[1], n.corner_radius)
        self.assertAlmostEqual(self.path[3].center[0], -(b - t))
        self.assertAlmostEqual(self.path[3].center[1], h - n.corner_radius)

    def test_enters_and_leaves_clear_of_the_panel(self):
        """The plunge must happen in air, past the edge the notch opens on."""
        self.assertLess(self.path[0].p0[1], -TOOL_R)
        self.assertLess(self.path[-1].p1[1], -TOOL_R)

    def test_path_is_symmetric(self):
        first, last = self.path[0], self.path[-1]
        self.assertAlmostEqual(first.p0[0], -last.p1[0])
        self.assertAlmostEqual(first.p0[1], last.p1[1])

    def test_cutter_too_big_for_the_inside_corner_is_refused(self):
        with self.assertRaises(ValueError) as e:
            self.n.toolpath(6.0, LEAD)  # a 12 mm cutter against R6
        self.assertIn("R6", str(e.exception))
        # Exactly R is still refused: it would leave a zero-radius arc.
        with self.assertRaises(ValueError):
            self.n.toolpath(self.n.corner_radius, LEAD)


class TestPasses(unittest.TestCase):
    def test_depths_are_even_and_reach_through(self):
        panel = cs.Panel()
        depths = cs.pass_depths(panel, 3.0)
        self.assertEqual(len(depths), 5)
        self.assertAlmostEqual(depths[-1], panel.z_through)
        steps = [depths[0]] + [b - a for a, b in zip(depths, depths[1:])]
        for s in steps:
            self.assertAlmostEqual(abs(s), 2.5)

    def test_no_pass_exceeds_the_stepdown(self):
        for stepdown in (1.0, 2.0, 2.5, 3.0, 4.0, 12.5, 40.0):
            depths = cs.pass_depths(cs.Panel(), stepdown)
            steps = [depths[0]] + [b - a for a, b in zip(depths, depths[1:])]
            self.assertLessEqual(max(abs(s) for s in steps), stepdown + 1e-9)

    def test_a_thicker_panel_gets_more_passes(self):
        thin = cs.pass_depths(cs.Panel(thickness=12.0), 3.0)
        thick = cs.pass_depths(cs.Panel(thickness=18.0), 3.0)
        self.assertGreater(len(thick), len(thin))


class TestProgram(unittest.TestCase):
    def setUp(self):
        self.notch, self.panel, self.plan, self.machine = build()
        self.text = cs.program(self.notch, self.panel, self.plan, self.machine)
        self.blocks = parse(self.text)
        self.setup = setup_from(self.notch, self.panel, self.plan)
        self.res = backplot(self.blocks, self.setup)

    def test_program_is_silent(self):
        """The generator must not produce work the verifier objects to."""
        diags = lint(self.blocks, self.res, self.setup)
        self.assertEqual([str(d) for d in diags], [])

    def test_never_cuts_past_the_spoilboard_skim(self):
        lowest = min(m.end[2] for m in self.res.moves)
        self.assertAlmostEqual(lowest, self.panel.z_through)

    def test_stays_within_the_notch(self):
        """The tool centre never reaches the notch floor, only r short of it."""
        deepest_y = max(m.end[1] for m in self.res.moves)
        self.assertAlmostEqual(deepest_y, self.notch.depth - self.plan.tool_radius)
        widest = max(abs(m.end[0]) for m in self.res.moves)
        # The program carries three decimals, so allow the micron of rounding.
        self.assertAlmostEqual(widest, self.notch.mouth_span / 2.0, delta=0.001)

    def test_every_plunge_happens_in_air(self):
        """A straight-down move may only happen clear of the panel edge."""
        for m in self.res.moves:
            dropping = m.end[2] < m.start[2] - 1e-9
            flat = math.dist(m.start[:2], m.end[:2]) < 1e-9
            if dropping and flat:
                self.assertLess(m.end[1], 0.0,
                                f"line {m.line_no} plunges at Y{m.end[1]:.3f}")

    def test_tabs_are_cut_on_the_last_pass_only(self):
        tab_z = -self.panel.thickness + self.plan.tab_height
        lifted = [m for m in self.res.moves
                  if m.kind == "feed" and abs(m.end[2] - tab_z) < 1e-9]
        # Two tabs, each reached by a ramp up and left by a ramp down.
        self.assertEqual(len(lifted), 4)
        for m in lifted:
            self.assertLess(m.start[2], tab_z + 1e-9)

    def test_tabs_leave_material_under_the_cut(self):
        tab_z = -self.panel.thickness + self.plan.tab_height
        self.assertGreater(tab_z, self.panel.z_through)
        self.assertLess(tab_z, 0.0)

    def test_tabs_can_be_turned_off(self):
        plan = cs.CutPlan(rpm=17900.0, feed=2400.0, tab_width=0.0)
        text = cs.program(self.notch, self.panel, plan, self.machine)
        blocks = parse(text)
        res = backplot(blocks, setup_from(self.notch, self.panel, plan))
        depths = {round(m.end[2], 3) for m in res.moves}
        self.assertNotIn(-10.5, depths)

    def test_a_tab_longer_than_the_flank_is_refused(self):
        plan = cs.CutPlan(rpm=17900.0, feed=2400.0, tab_width=40.0)
        with self.assertRaises(ValueError) as e:
            cs.program(self.notch, self.panel, plan, self.machine)
        self.assertIn("flank", str(e.exception))

    def test_header_states_the_datum_and_the_spans(self):
        head = self.text.split("G21")[0]
        self.assertIn("71.651", head)
        self.assertIn("X0 = notch centreline", head)
        self.assertIn("Z0 = TOP FACE", head)
        self.assertIn("no G41/G42", head)

    def test_no_cutter_compensation_is_emitted(self):
        """The offset is in the coordinates; the control is not asked to help."""
        for b in self.blocks:
            self.assertFalse(b.has_g(41.0, 42.0), f"line {b.line_no}: {b.raw}")
            self.assertFalse(b.has("D"))

    def test_ends_in_a_known_state(self):
        self.assertTrue(any(b.has_m(5.0) for b in self.blocks))
        self.assertTrue(any(b.has_m(30.0) for b in self.blocks))
        self.assertFalse(self.res.end_state.spindle_on)

    def test_a_measured_cutter_moves_the_path(self):
        """The whole point of computing comp here: pass what you measured."""
        worn = self.notch.toolpath(3.96, LEAD)       # a 7.92 mm cutter
        nominal = self.notch.toolpath(4.0, LEAD)
        self.assertAlmostEqual(worn[4].p0[1] - nominal[4].p0[1], 0.04, places=9)


class TestVerifierGating(unittest.TestCase):
    """The router profile turns off rules that only make sense on a VMC."""

    NC = "G21 G17 G40 G90\nG54\nM3 S12000\nG0 Z5.\nG1 Z-1. F300\nM5\nM30\n"

    def rules(self, machine_name: str) -> set[str]:
        blocks = parse(self.NC)
        setup = Setup(machine=Machine.load(machine_name))
        return {d.rule for d in lint(blocks, backplot(blocks, setup), setup)}

    def test_router_does_not_demand_tool_length_comp(self):
        self.assertNotIn("NO_TOOL_LENGTH_COMP", self.rules("stepcraft_m1000"))
        self.assertNotIn("NO_SAFE_START", self.rules("stepcraft_m1000"))

    def test_a_vmc_still_demands_it(self):
        found = self.rules("haas_vf2")
        self.assertIn("NO_TOOL_LENGTH_COMP", found)
        self.assertIn("NO_SAFE_START", found)  # no G49/G80 in that header

    def test_long_comment_header_does_not_hide_the_safe_start_line(self):
        nc = "\n".join(["(banner)"] * 20) + "\n" + self.NC
        blocks = parse(nc)
        setup = Setup(machine=Machine.load("stepcraft_m1000"))
        rules = {d.rule for d in lint(blocks, backplot(blocks, setup), setup)}
        self.assertNotIn("NO_SAFE_START", rules)


class TestFeeds(unittest.TestCase):
    def test_mdf_on_a_router_is_feed_limited_not_power_limited(self):
        """A desktop router runs out of feedrate long before it runs out of watts."""
        machine = Machine.load("stepcraft_m1000")
        res = feeds.compute(feeds.Cut(
            material="mdf", diameter=8.0, flutes=2, ae=8.0, ap=2.5,
            stickout=30.0, max_rpm=machine.max_rpm,
            max_feed=machine.max_feed_mm_min))
        self.assertTrue(any("feed limited" in c for c in res.clamped))
        self.assertLessEqual(res.feed, machine.max_feed_mm_min)
        # Under a kilowatt at the cutter, which is what a 1 kW router has.
        self.assertLess(res.power_kw, 1.0)
        # And the chip load it actually achieves is below what MDF wants,
        # which is the honest reason these cuts burn if you dawdle.
        self.assertLess(res.fz_commanded, res.fz_target)

    def test_derive_feed_fills_in_and_scales(self):
        notch, panel, _, machine = build()
        plan = cs.CutPlan()
        cs.derive_feed(plan, panel, machine)
        self.assertGreater(plan.rpm, 0)
        self.assertAlmostEqual(plan.feed, machine.max_feed_mm_min * plan.feed_scale,
                               delta=10.0)
        self.assertTrue(any("feeds.py" in d for d in plan.derivation))

    def test_explicit_feeds_are_left_alone(self):
        _, panel, _, machine = build()
        plan = cs.CutPlan(rpm=12000.0, feed=1500.0)
        cs.derive_feed(plan, panel, machine)
        self.assertEqual((plan.rpm, plan.feed), (12000.0, 1500.0))


if __name__ == "__main__":
    unittest.main()
