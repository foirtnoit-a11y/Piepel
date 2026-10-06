"""Tests for the shaft-slot chamfer path generator and the DXF writer."""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "machining"))

from chamfer import (ChamferTool, Shaft, Slot, build, hand_offset_at,  # noqa: E402
                     measurement_sheet, measurement_stations, report,
                     simple_construction, to_dxf)
from dxf import Dxf, arc_segments, chordal_error  # noqa: E402

# The case this was written for: 50 mm shaft, 12 mm slot, 40 mm long, 0.4 chamfer.
CASE = dict(shaft=50.0, width=12.0, length=40.0, leg=0.4)


def make(shaft=50.0, width=12.0, length=40.0, leg=0.4, angle=90.0,
         ends="round", **tool_kw):
    return build(Shaft(shaft), Slot(width, length, ends),
                 ChamferTool(included_angle=angle, **tool_kw), leg=leg)


class TestShaftGeometry(unittest.TestCase):
    def test_edge_drop_matches_the_hand_calculation(self):
        """sqrt(25^2 - 6^2) = 24.2693, so the edge sits 0.7307 below the top."""
        s = Shaft(50.0)
        self.assertAlmostEqual(s.drop(6.0), 25.0 - math.sqrt(625.0 - 36.0), places=12)
        self.assertAlmostEqual(s.drop(6.0), 0.7307, places=4)

    def test_no_drop_on_the_centreline(self):
        self.assertAlmostEqual(Shaft(50.0).drop(0.0), 0.0, places=12)

    def test_drop_off_the_shaft_is_rejected(self):
        with self.assertRaises(ValueError):
            Shaft(50.0).drop(30.0)


class TestCompensation(unittest.TestCase):
    """The core property: the chamfer leg must come out constant."""

    @staticmethod
    def _leg_cut(path, x, offset):
        """Leg actually produced at position x by a path offset `offset` outward."""
        return offset - (path.z_apex + path.shaft.drop(x)) * path.tool.tan_half

    def test_exact_path_holds_the_leg_everywhere(self):
        for angle in (60.0, 90.0, 120.0):
            p = make(angle=angle)
            tan = p.tool.tan_half
            for i in range(181):
                x = p.slot.half_width * math.cos(math.radians(i))
                got = self._leg_cut(p, x, p.shaft.drop(x) * tan)
                with self.subTest(angle=angle, x=round(x, 3)):
                    self.assertAlmostEqual(got, p.leg, places=10)

    def test_offsets_at_the_two_extremes(self):
        p = make()
        self.assertAlmostEqual(p.offset_at_flank, 0.7307, places=4)
        self.assertAlmostEqual(p.shaft.drop(0.0) * p.tool.tan_half, 0.0, places=12)

    def test_program_z_is_one_vertical_leg_down(self):
        self.assertAlmostEqual(make(leg=0.4).z_apex, -0.4, places=12)
        # A 60 degree included tool has a 30 degree flank, so the same radial
        # leg needs a deeper Z.
        p = make(leg=0.4, angle=60.0)
        self.assertAlmostEqual(p.z_apex, -0.4 / math.tan(math.radians(30.0)), places=12)
        self.assertAlmostEqual(p.leg_vertical, 0.4 / math.tan(math.radians(30.0)), places=12)

    def test_flank_offset_scales_with_the_tool_angle(self):
        for angle in (60.0, 90.0, 120.0):
            p = make(angle=angle)
            expected = p.shaft.drop(6.0) * math.tan(math.radians(angle / 2.0))
            with self.subTest(angle=angle):
                self.assertAlmostEqual(p.offset_at_flank, expected, places=12)

    def test_path_touches_the_true_outline_at_the_apex(self):
        """Offset is zero where the edge is back at full height."""
        p = make()
        pts = [(round(x, 9), round(y, 9)) for x, y in p.points()]
        self.assertIn((0.0, 20.0), pts)
        self.assertIn((0.0, -20.0), pts)

    def test_widest_point_is_the_flank(self):
        p = make()
        self.assertAlmostEqual(max(abs(x) for x, _ in p.points()),
                               6.0 + p.offset_at_flank, places=9)


class TestSimpleConstruction(unittest.TestCase):
    """The by-hand version, and an honest account of what it costs."""

    def test_reproduces_the_shop_numbers(self):
        s = simple_construction(Shaft(50.0), Slot(12.0, 40.0), ChamferTool(90.0), 0.4)
        self.assertAlmostEqual(s["length"], 40.0, places=9)
        self.assertAlmostEqual(s["width"], 13.4614, places=4)   # 12 + 2 x 0.7307
        self.assertAlmostEqual(s["corner_radius"], 6.0, places=9)

    def test_error_peaks_near_sixty_degrees(self):
        """Measured as the tool's closest approach to the edge, which is what
        decides the cut. Measuring point-to-corresponding-point instead
        overstates it by about 23%."""
        s = simple_construction(Shaft(50.0), Slot(12.0, 40.0), ChamferTool(90.0), 0.4)
        self.assertAlmostEqual(s["max_error"], 0.1501, places=3)
        self.assertAlmostEqual(s["max_error_at"], 58.0, delta=3.0)
        self.assertLess(s["max_error"], Shaft(50.0).drop(6.0) / 4.0)

    def test_hand_construction_never_undercuts(self):
        """The reason the shop method works: an over-cut still removes the burr.

        If it ever sat closer to the edge than required it would leave burr
        behind, which is the only failure that matters for a deburr.
        """
        sh, sl, t = Shaft(50.0), Slot(12.0, 40.0), ChamferTool(90.0)
        # The hand curve is sampled as a polyline, whose chords sit up to
        # r(1-cos(step/2)) ~ 5e-5 mm inside the true arc. Anything within that
        # is the sampling, not an undercut.
        tol = 1e-4
        for deg in range(0, 91, 5):
            required = sh.drop(sl.half_width * math.cos(math.radians(deg))) * t.tan_half
            with self.subTest(theta=deg):
                self.assertGreaterEqual(hand_offset_at(sh, sl, t, deg) - required, -tol)

    def test_error_is_an_overcut_not_an_undercut(self):
        """It cuts too much, which cannot be corrected on a second pass."""
        p = make()
        x = 6.0 * math.cos(math.radians(60.0))
        hand = p.offset_at_flank * math.cos(math.radians(60.0))
        leg = hand - (p.z_apex + p.shaft.drop(x)) * p.tool.tan_half
        self.assertGreater(leg, p.leg)
        self.assertAlmostEqual(leg, 0.5847, places=4)

    def test_error_is_reported_as_a_fraction_of_the_chamfer(self):
        big = make(leg=2.0)
        small = make(leg=0.2)
        self.assertLess(big.simple["error_fraction"], small.simple["error_fraction"])
        self.assertGreater(small.simple["error_fraction"], 0.5)

    def test_warning_states_it_over_cuts_rather_than_leaving_burr(self):
        w = " ".join(make(leg=0.2).warnings)
        self.assertIn("never under-cuts", w)
        self.assertIn("always deburrs", w)

    def test_square_ends_are_worse_than_round(self):
        rnd = make(ends="round").simple["max_error"]
        sqr = make(ends="square").simple["max_error"]
        self.assertGreater(sqr, rnd)


class TestEndStyles(unittest.TestCase):
    def test_open_slot_is_two_straight_lines_at_constant_offset(self):
        p = make(ends="open", length=60.0)
        self.assertEqual([k for k, _ in p.segments], ["line", "line"])
        self.assertFalse(p.closed)
        for _, pts in p.segments:
            self.assertAlmostEqual(abs(pts[0][0]), 6.0 + p.offset_at_flank, places=9)
            self.assertAlmostEqual(pts[0][0], pts[1][0], places=12)

    def test_square_ends_become_curves_once_offset(self):
        p = make(ends="square")
        end = next(pts for kind, pts in p.segments if kind == "poly")
        ys = {round(y, 6) for _, y in end}
        self.assertGreater(len(ys), 5)  # no longer a straight line

    def test_round_slot_shorter_than_wide_is_rejected(self):
        with self.assertRaises(ValueError):
            Slot(12.0, 8.0, "round")

    def test_slot_wider_than_the_shaft_is_rejected(self):
        with self.assertRaises(ValueError):
            build(Shaft(10.0), Slot(12.0, 40.0), ChamferTool(), 0.4)

    def test_unknown_end_style_is_rejected(self):
        with self.assertRaises(ValueError):
            Slot(12.0, 40.0, "pointy")


class TestToolHandling(unittest.TestCase):
    def test_tip_flat_raises_the_programmed_z(self):
        """A flat-tipped tool sits above its own virtual point."""
        p = make(tip_diameter=0.2)
        self.assertAlmostEqual(p.tool.tip_rise, 0.1, places=12)  # (0.2/2)/tan45
        self.assertAlmostEqual(p.z_flat, p.z_apex + 0.1, places=12)
        self.assertIn("virtual point", " ".join(p.warnings))

    def test_sharp_tool_needs_no_z_correction(self):
        p = make()
        self.assertEqual(p.tool.tip_rise, 0.0)
        self.assertEqual(p.z_flat, p.z_apex)

    def test_tool_too_small_for_the_leg_is_flagged(self):
        self.assertIn("runs out of flank", " ".join(make(leg=3.0, diameter=4.0).warnings))

    def test_deep_chamfer_versus_shallow_slot_is_flagged(self):
        p = build(Shaft(50.0), Slot(12.0, 40.0, "round", depth=0.3), ChamferTool(), 0.4)
        self.assertIn("slot is only", " ".join(p.warnings))

    def test_negative_leg_is_rejected(self):
        with self.assertRaises(ValueError):
            make(leg=-0.1)


class TestReportAndDxf(unittest.TestCase):
    def test_report_states_the_key_numbers(self):
        r = report(make())
        for token in ("0.7307", "24.2693", "-0.4000", "13.4614", "corner R6"):
            with self.subTest(token=token):
                self.assertIn(token, r)

    def test_dxf_is_well_formed_r12(self):
        d = to_dxf(make())
        s = d.to_string()
        self.assertIn("AC1009", s)          # R12
        self.assertIn("$INSUNITS", s)
        self.assertTrue(s.rstrip().endswith("EOF"))
        self.assertEqual(s.count("0\nSECTION\n"), 3)
        for layer in ("CHAMFER_PATH", "SLOT_NOMINAL", "SHAFT_OD"):
            self.assertIn(layer, s)

    def test_dxf_without_reference_geometry_has_only_the_path(self):
        s = to_dxf(make(), include_reference=False).to_string()
        self.assertIn("CHAMFER_PATH", s)
        self.assertNotIn("SLOT_NOMINAL", s)

    def test_chordal_tolerance_is_respected(self):
        p = build(Shaft(50.0), Slot(12.0, 40.0), ChamferTool(), 0.4, chordal_tol=0.001)
        self.assertLessEqual(p.chord_error, 0.001 + 1e-9)

    def test_tighter_tolerance_means_more_points(self):
        coarse = build(Shaft(50.0), Slot(12.0, 40.0), ChamferTool(), 0.4, chordal_tol=0.02)
        fine = build(Shaft(50.0), Slot(12.0, 40.0), ChamferTool(), 0.4, chordal_tol=0.001)
        self.assertGreater(len(fine.points()), len(coarse.points()))

    def test_arc_segment_helpers_agree(self):
        n = arc_segments(6.0, 180.0, 0.005)
        self.assertLessEqual(chordal_error(6.0, 180.0, n), 0.005 + 1e-12)

    def test_dxf_rejects_unknown_units(self):
        with self.assertRaises(ValueError):
            Dxf("furlongs")


class TestMeasurementStations(unittest.TestCase):
    """The table you take to the comparator. It has to be falsifiable."""

    def test_exact_path_predicts_one_width_everywhere(self):
        rows = measurement_stations(make())
        widths = {round(r["face_exact"], 9) for r in rows}
        self.assertEqual(len(widths), 1)
        self.assertAlmostEqual(rows[0]["face_exact"], 0.4 * math.sqrt(2), places=9)

    def test_face_width_is_the_leg_over_sin_of_the_flank_angle(self):
        for angle in (60.0, 90.0, 120.0):
            p = make(angle=angle)
            expected = p.leg / math.sin(p.tool.half_angle)
            with self.subTest(angle=angle):
                self.assertAlmostEqual(measurement_stations(p)[0]["face_exact"],
                                       expected, places=9)
                self.assertAlmostEqual(p.face_width, expected, places=9)

    def test_routes_agree_at_the_flank_and_the_apex(self):
        rows = {r["theta"]: r for r in measurement_stations(make())}
        for th in (0.0, 90.0):
            self.assertAlmostEqual(rows[th]["difference"], 0.0, places=4)

    def test_routes_disagree_most_at_sixty_degrees(self):
        rows = measurement_stations(make())
        worst = max(rows, key=lambda r: r["difference"])
        self.assertEqual(worst["theta"], 60.0)
        self.assertAlmostEqual(worst["face_hand"], 0.7767, places=3)
        self.assertAlmostEqual(worst["face_exact"], 0.5657, places=4)

    def test_stations_sit_on_the_true_slot_outline(self):
        """The operator measures the real edge, not the offset path."""
        p = make()
        for r in measurement_stations(p):
            with self.subTest(theta=r["theta"]):
                # distance from the end-arc centre must be the slot half width
                d = math.hypot(r["x"], r["y"] - p.slot.arc_center_y)
                self.assertAlmostEqual(d, p.slot.half_width, places=9)

    def test_surface_drop_is_reported_as_a_negative_z(self):
        rows = measurement_stations(make())
        self.assertAlmostEqual(rows[0]["surface_drop"], 0.7307, places=4)
        self.assertAlmostEqual(rows[-1]["surface_drop"], 0.0, places=9)

    def test_sheet_names_the_deciding_station(self):
        sheet = measurement_sheet(make())
        self.assertIn("theta=60", sheet)
        self.assertIn("0.7767", sheet)
        self.assertIn("1.37x wider", sheet)


class TestChaining(unittest.TestCase):
    """The DXF has to chain into one contour, or OneCNC leaves gaps in the chamfer."""

    def test_closed_paths_have_no_handover_gaps(self):
        for ends in ("round", "square"):
            with self.subTest(ends=ends):
                gap, closing = make(ends=ends).continuity()
                self.assertAlmostEqual(gap, 0.0, places=9)
                self.assertAlmostEqual(closing, 0.0, places=9)

    def test_square_corners_are_joined_by_a_round_sweep(self):
        """The flank offsets in X and the end in Y; without a join they never meet."""
        p = make(ends="square")
        hw, hl, o = 6.0, 20.0, p.offset_at_flank
        pts = [(round(x, 6), round(y, 6)) for x, y in p.points()]
        # both ends of the quarter turn at the (+x, +y) corner are on the path
        self.assertIn((round(hw + o, 6), round(hl, 6)), pts)
        self.assertIn((round(hw, 6), round(hl + o, 6)), pts)
        # and the sweep between them stays at the offset distance from the corner
        mids = [(x, y) for x, y in p.points()
                if hw < x < hw + o and hl < y < hl + o]
        self.assertTrue(mids)
        for x, y in mids:
            self.assertAlmostEqual(math.hypot(x - hw, y - hl), o, places=6)

    def test_open_slot_reports_no_handover(self):
        gap, closing = make(ends="open", length=60.0).continuity()
        self.assertEqual((gap, closing), (0.0, 0.0))

    def test_dxf_entities_are_endpoint_continuous(self):
        """Walk the emitted LINE entities and confirm each starts where the last ended."""
        p = make()
        d = to_dxf(p, include_reference=False)
        lines = d.to_string().split("\n")
        coords, i = [], 0
        while i < len(lines):
            if lines[i] == "LINE":
                vals = {}
                j = i + 1
                while j + 1 < len(lines) and lines[j] != "0":
                    vals[lines[j]] = lines[j + 1]
                    j += 2
                coords.append(((float(vals["10"]), float(vals["20"])),
                               (float(vals["11"]), float(vals["21"]))))
                i = j
            else:
                i += 1
        self.assertGreater(len(coords), 10)
        for (_, end), (start, _) in zip(coords, coords[1:]):
            self.assertAlmostEqual(math.dist(end, start), 0.0, places=6)
        self.assertAlmostEqual(math.dist(coords[-1][1], coords[0][0]), 0.0, places=6)


class TestPathExtents(unittest.TestCase):
    def test_path_stays_within_the_compensated_footprint(self):
        p = make()
        xs = [abs(x) for x, _ in p.points()]
        ys = [abs(y) for _, y in p.points()]
        self.assertAlmostEqual(max(xs), 6.0 + p.offset_at_flank, places=9)
        self.assertAlmostEqual(max(ys), 20.0, places=9)

    def test_report_states_the_chaining_numbers(self):
        self.assertIn("handover gap 0.000000", report(make()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
