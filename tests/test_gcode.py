"""Tests for the G-code verifier. Stdlib only: `python3 -m unittest discover tests`."""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "machining"))

from gcode import parse, backplot, lint, Machine, Setup, Stock, Tool  # noqa: E402
from gcode.parser import program_number  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def make_setup(**kw) -> Setup:
    return Setup(
        machine=Machine.load("haas_vf2"),
        stock=Stock(x_min=-50, x_max=50, y_min=-40, y_max=40, z_bottom=-25, z_top=0),
        tools={1: Tool(1, 12.0, "12mm 4FL", flutes=4), 2: Tool(2, 6.8, "6.8 drill")},
        **kw,
    )


def rules_for(nc: str, setup: Setup | None = None) -> set[str]:
    setup = setup or make_setup()
    blocks = parse(nc)
    return {d.rule for d in lint(blocks, backplot(blocks, setup), setup)}


class TestParser(unittest.TestCase):
    def test_words_comments_and_block_delete(self):
        b = parse("/N10 G01 X-1.5 Y.25 (rough) ; note")[0]
        self.assertTrue(b.block_delete)
        self.assertEqual(b.get("X"), -1.5)
        self.assertEqual(b.get("Y"), 0.25)
        self.assertEqual(b.comments, ["rough", "note"])

    def test_spaced_words_and_repeated_g(self):
        b = parse("G 17 G90 G54")[0]
        self.assertEqual(b.g_codes(), [17.0, 90.0, 54.0])
        self.assertTrue(b.has_g(54.0))

    def test_program_number_and_tape_marks(self):
        blocks = parse("%\nO2050 (PART)\nM30\n%")
        self.assertEqual(program_number(blocks), 2050)
        self.assertTrue(blocks[0].tape_mark)


class TestArcs(unittest.TestCase):
    """R-form sign convention: |R| is the minor arc, -R the major arc."""

    def _arc(self, cmd):
        setup = make_setup()
        res = backplot(parse(f"G21 G17 G90 G0 X10. Y0.\n{cmd} F500."), setup)
        return next(m for m in res.moves if m.kind.startswith("arc"))

    def test_r_form_all_four_cases(self):
        quarter, three_quarter = math.pi * 10 / 2, 3 * math.pi * 10 / 2
        for cmd, centre, length in (
            ("G3 X0. Y10. R10.", (0, 0), quarter),
            ("G3 X0. Y10. R-10.", (10, 10), three_quarter),
            ("G2 X0. Y10. R10.", (10, 10), quarter),
            ("G2 X0. Y10. R-10.", (0, 0), three_quarter),
        ):
            with self.subTest(cmd=cmd):
                a = self._arc(cmd)
                self.assertAlmostEqual(math.dist(a.center[:2], centre), 0.0, places=6)
                self.assertAlmostEqual(a.length, length, places=6)

    def test_ij_full_circle(self):
        a = self._arc("G3 X10. Y0. I-10. J0.")
        self.assertAlmostEqual(a.length, 2 * math.pi * 10, places=6)

    def test_inconsistent_ij_is_caught(self):
        self.assertIn("ARC_RADIUS_MISMATCH", rules_for("G21 G90 G0 X10. Y0.\nG2 X0. Y12. I-10. J0. F500."))

    def test_impossible_r_is_caught(self):
        self.assertIn("ARC_R_IMPOSSIBLE", rules_for("G21 G90 G0 X0. Y0.\nG2 X100. Y0. R10. F500."))


class TestCannedCycles(unittest.TestCase):
    NC = ("G21 G17 G40 G49 G80 G90\nG53 G0 Z0.\nT1 M6\nG54 G0 X0. Y0. S3000 M3\n"
          "G43 H1 Z25.\nG99 G83 X0. Y0. Z-15. R2. Q3. F150.\nX20.\nG80\nG53 G0 Z0.\nM30")

    def test_peck_expands_to_one_hole_per_position(self):
        setup = make_setup()
        res = backplot(parse(self.NC), setup)
        bottoms = [m for m in res.moves if m.from_cycle and m.kind == "cycle_feed"
                   and abs(m.end[2] + 15) < 1e-6]
        self.assertEqual(len(bottoms), 2)

    def test_peck_retracts_do_not_read_as_plunge_crashes(self):
        self.assertNotIn("RAPID_PLUNGE_INTO_STOCK", rules_for(self.NC))

    def test_uncancelled_cycle_is_reported(self):
        self.assertIn("CYCLE_LEFT_ON", rules_for(self.NC.replace("G80\n", "")))


class TestCrashRules(unittest.TestCase):
    def test_rapid_across_part_at_depth(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X-40. Y0. S3000 M3\nG43 H1 Z2.\n"
              "G1 Z-5. F200.\nG0 X40.\nG53 G0 Z0.\nM30")
        self.assertIn("RAPID_XY_IN_STOCK", rules_for(nc))

    def test_rapid_plunge_into_material(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\nG43 H1 Z2.\n"
              "G0 Z-5.\nG53 G0 Z0.\nM30")
        self.assertIn("RAPID_PLUNGE_INTO_STOCK", rules_for(nc))

    def test_cutting_below_the_stock_bottom(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\nG43 H1 Z2.\n"
              "G1 Z-30. F200.\nG53 G0 Z0.\nM30")
        self.assertIn("CUT_BELOW_STOCK", rules_for(nc))

    def test_retracting_from_depth_is_not_a_cut_below_stock(self):
        """A move that starts deep and ends high is the escape, not the mistake."""
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\nG43 H1 Z2.\n"
              "G1 Z-20. F200.\nG0 Z25.\nG53 G0 Z0.\nM30")
        self.assertNotIn("CUT_BELOW_STOCK", rules_for(nc))

    def test_missing_tool_length_offset(self):
        nc = "G21 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\nZ2.\nM30"
        self.assertIn("NO_TOOL_LENGTH_COMP", rules_for(nc))

    def test_h_number_must_match_tool(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\nG43 H7 Z2.\nM30")
        self.assertIn("H_TOOL_MISMATCH", rules_for(nc))

    def test_cutting_with_spindle_stopped(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0.\nG43 H1 Z2.\n"
              "G1 X10. F300.\nM30")
        self.assertIn("CUT_WITH_SPINDLE_OFF", rules_for(nc))

    def test_motion_before_a_work_offset(self):
        nc = "G21 G17 G40 G49 G80 G90\nT1 M6\nG0 X0. Y0. S3000 M3\nG43 H1 Z2.\nM30"
        self.assertIn("NO_WORK_OFFSET", rules_for(nc))


class TestMachineLimits(unittest.TestCase):
    def test_spindle_over_machine_maximum(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S15000 M3\nG43 H1 Z2.\nM30")
        self.assertIn("OVER_MAX_RPM", rules_for(nc))

    def test_unknown_tool_number(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT9 M6\nG54 G0 X0. Y0. S3000 M3\nG43 H9 Z2.\nM30")
        self.assertIn("TOOL_NOT_IN_SETUP", rules_for(nc))

    def test_travel_limits_checked_only_with_a_known_offset(self):
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X-900. Y0. S3000 M3\nG43 H1 Z2.\nM30")
        self.assertNotIn("OUTSIDE_ENVELOPE", rules_for(nc))
        setup = make_setup()
        setup.wcs_offset = (-100.0, -100.0, -100.0)
        self.assertIn("OUTSIDE_ENVELOPE", rules_for(nc, setup))


class TestUnitsAndModes(unittest.TestCase):
    def test_inch_input_is_converted_to_mm(self):
        res = backplot(parse("G20 G90 G0 X1.\nG1 X2. F10."), make_setup())
        self.assertAlmostEqual(res.moves[-1].end[0], 50.8, places=6)
        self.assertAlmostEqual(res.moves[-1].feed, 254.0, places=6)
        self.assertEqual(res.units, "inch")

    def test_incremental_distance_mode(self):
        res = backplot(parse("G21 G90 G0 X10. Y10.\nG91 G1 X5. Y-2. F100.\nG90"), make_setup())
        self.assertAlmostEqual(res.moves[-1].end[0], 15.0)
        self.assertAlmostEqual(res.moves[-1].end[1], 8.0)

    def test_g95_feed_per_revolution(self):
        res = backplot(parse("G21 G90 G95 S1000 M3\nG1 X10. F0.2"), make_setup())
        self.assertAlmostEqual(res.moves[-1].feed, 200.0)  # 0.2 mm/rev * 1000 rpm


class TestFixtureProgram(unittest.TestCase):
    def test_clean_program_is_silent(self):
        """The most important test: no false positives on correct code."""
        nc = (FIXTURES / "good_bracket.nc").read_text()
        self.assertEqual(rules_for(nc), set())

    def test_broken_program_is_caught(self):
        nc = (FIXTURES / "bad_bracket.nc").read_text()
        found = rules_for(nc)
        for expected in ("RAPID_XY_IN_STOCK", "RAPID_PLUNGE_INTO_STOCK", "CUT_BELOW_STOCK",
                         "NO_TOOL_LENGTH_COMP", "H_TOOL_MISMATCH", "CUT_WITH_SPINDLE_OFF",
                         "OVER_MAX_RPM", "NO_PROGRAM_END", "COMP_LEFT_ON"):
            with self.subTest(rule=expected):
                self.assertIn(expected, found)

    def test_cycle_time_is_plausible(self):
        nc = (FIXTURES / "good_bracket.nc").read_text()
        res = backplot(parse(nc), make_setup())
        self.assertGreater(res.cycle_seconds, 10)
        self.assertLess(res.cycle_seconds, 600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
