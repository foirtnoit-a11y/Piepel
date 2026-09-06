"""Tests for the Fusion -> verifier setup contract.

These run outside Fusion on purpose. The stock block that `cam.py` writes is
the same one the G-code linter checks against, so if this maths is wrong the
crash rules are wrong too.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fusion"))
sys.path.insert(0, str(ROOT / "machining"))

from fusionkit.cam import MachiningSetup, ToolSpec, operator_sheet, stock_from_bbox  # noqa: E402
from fusionkit.units import expr, from_internal, to_internal  # noqa: E402
from gcode import Machine, Setup, Stock, backplot, lint, parse  # noqa: E402


class TestUnits(unittest.TestCase):
    def test_round_trip(self):
        self.assertAlmostEqual(from_internal(to_internal(42.0)), 42.0, places=9)

    def test_internal_is_centimetres(self):
        """The gotcha: createByReal(1) is one centimetre, not one millimetre."""
        self.assertAlmostEqual(to_internal(10.0), 1.0, places=9)
        self.assertAlmostEqual(from_internal(1.0), 10.0, places=9)

    def test_expressions_carry_their_unit(self):
        self.assertEqual(expr(12.5), "12.5 mm")


class TestStockGeneration(unittest.TestCase):
    BBOX = ((-40.0, -25.0, -18.0), (40.0, 25.0, 0.0))  # 80 x 50 x 18 part

    def test_centre_top_datum(self):
        s = stock_from_bbox(*self.BBOX, MachiningSetup(
            datum="center_top", stock_allowance_xy=2.0, stock_allowance_top=1.0))
        self.assertAlmostEqual(s["x_max"] - s["x_min"], 84.0)
        self.assertAlmostEqual(s["y_max"] - s["y_min"], 54.0)
        self.assertAlmostEqual(s["z_top"], 0.0)
        self.assertAlmostEqual(s["z_bottom"], -19.0)
        self.assertAlmostEqual(s["x_min"], -42.0)

    def test_corner_top_datum_puts_the_origin_at_a_corner(self):
        s = stock_from_bbox(*self.BBOX, MachiningSetup(datum="corner_top"))
        self.assertAlmostEqual(s["x_min"], 0.0)
        self.assertAlmostEqual(s["y_min"], 0.0)
        self.assertAlmostEqual(s["z_top"], 0.0)

    def test_centre_bottom_datum_sits_on_the_vise_jaws(self):
        s = stock_from_bbox(*self.BBOX, MachiningSetup(datum="center_bottom"))
        self.assertAlmostEqual(s["z_bottom"], 0.0)
        self.assertGreater(s["z_top"], 0.0)

    def test_unknown_datum_is_rejected(self):
        with self.assertRaises(ValueError):
            stock_from_bbox(*self.BBOX, MachiningSetup(datum="somewhere_else"))


class TestContractWithVerifier(unittest.TestCase):
    """The whole point: stock written by Fusion must drive the linter correctly."""

    def _setup(self):
        s = stock_from_bbox((-40.0, -25.0, -18.0), (40.0, 25.0, 0.0),
                            MachiningSetup(datum="center_top"))
        return Setup(machine=Machine.load("haas_vf2"), stock=Stock(**s),
                     tools={1: __import__("gcode").Tool(1, 10.0)})

    def test_a_cut_past_the_stock_bottom_is_caught(self):
        setup = self._setup()  # stock bottom is Z-19
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\n"
              "G43 H1 Z2.\nG1 Z-22. F200.\nG53 G0 Z0.\nM30")
        blocks = parse(nc)
        rules = {d.rule for d in lint(blocks, backplot(blocks, setup), setup)}
        self.assertIn("CUT_BELOW_STOCK", rules)

    def test_a_cut_within_the_stock_is_accepted(self):
        setup = self._setup()
        nc = ("G21 G17 G40 G49 G80 G90\nT1 M6\nG54 G0 X0. Y0. S3000 M3\n"
              "G43 H1 Z2.\nG1 Z-15. F200.\nG1 X20. F600.\nG0 Z5.\nM5\nG53 G0 Z0.\nM30")
        blocks = parse(nc)
        rules = {d.rule for d in lint(blocks, backplot(blocks, setup), setup)}
        self.assertEqual(rules, set())

    def test_setup_dict_is_json_round_trippable(self):
        s = stock_from_bbox((-40.0, -25.0, -18.0), (40.0, 25.0, 0.0), MachiningSetup())
        self.assertEqual(json.loads(json.dumps(s)), s)
        Stock(**s)  # the verifier must accept it verbatim


class TestOperatorSheet(unittest.TestCase):
    def _sheet(self):
        setup = MachiningSetup(datum="corner_top", tools=[
            ToolSpec(1, 12.0, "12mm 4FL carbide", flutes=4, stickout=35.0),
            ToolSpec(2, 6.8, "6.8mm stub drill", length_offset=12),
        ])
        d = {
            "machine": "haas_vf2", "expected_wcs": ["G54"],
            "stock": stock_from_bbox((-40, -25, -18), (40, 25, 0), setup),
            "tools": [t.__dict__ for t in setup.tools],
            "_part_bbox_mm": {"size": [80.0, 50.0, 18.0]}, "_datum": "corner_top",
            "_notes": ["soft jaws"],
        }
        return operator_sheet(d, "BRACKET-01")

    def test_sheet_states_the_stock_the_datum_and_the_tools(self):
        sheet = self._sheet()
        self.assertIn("BRACKET-01", sheet)
        self.assertIn("84.00 x 54.00 x 19.00 mm", sheet)
        self.assertIn("front-left corner", sheet)
        self.assertIn("T1", sheet)
        self.assertIn("soft jaws", sheet)

    def test_sheet_shows_a_nonstandard_length_offset(self):
        """T2 runs on H12; the operator must see that or the tool crashes."""
        self.assertIn("T2   H12", self._sheet())
