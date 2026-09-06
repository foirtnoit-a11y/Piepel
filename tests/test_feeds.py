"""Tests for the feeds and speeds calculator."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "machining"))

from feeds import MATERIALS, Cut, chip_thinning_factor, compute, engagement_angle  # noqa: E402


class TestChipThinning(unittest.TestCase):
    def test_no_thinning_at_or_above_half_diameter(self):
        for ae in (5.0, 6.0, 10.0):
            self.assertEqual(chip_thinning_factor(10.0, ae), 1.0)

    def test_known_values(self):
        # ae = 10% of D  ->  1 / sqrt(1 - 0.8^2) = 1.667
        self.assertAlmostEqual(chip_thinning_factor(10.0, 1.0), 1.0 / math.sqrt(1 - 0.64), places=6)
        self.assertAlmostEqual(chip_thinning_factor(12.0, 1.2), 1.6667, places=3)

    def test_capped_for_vanishing_engagement(self):
        self.assertLessEqual(chip_thinning_factor(10.0, 0.001), 4.0)

    def test_engagement_angle(self):
        self.assertAlmostEqual(engagement_angle(10.0, 5.0), 90.0, places=6)   # half D
        self.assertAlmostEqual(engagement_angle(10.0, 10.0), 180.0, places=6)  # slotting


class TestFeedMath(unittest.TestCase):
    def test_rpm_from_surface_speed(self):
        r = compute(Cut("alu6061", diameter=12, flutes=3, ae=6, ap=6))
        self.assertAlmostEqual(r.rpm, 400 * 1000 / (math.pi * 12), places=3)

    def test_feed_is_chip_load_times_flutes_times_rpm(self):
        r = compute(Cut("alu6061", diameter=12, flutes=3, ae=6, ap=6))
        self.assertAlmostEqual(r.feed, r.fz_commanded * 3 * r.rpm, places=6)

    def test_thinning_raises_the_programmed_feed(self):
        heavy = compute(Cut("alu6061", diameter=12, flutes=3, ae=6, ap=6))
        light = compute(Cut("alu6061", diameter=12, flutes=3, ae=1.2, ap=6))
        self.assertAlmostEqual(light.fz_commanded / heavy.fz_commanded, 1.6667, places=3)
        # ...but the real chip at the cut is the same size
        self.assertAlmostEqual(light.fz_target, heavy.fz_target, places=9)

    def test_mrr_and_power_agree_with_the_rule_of_thumb(self):
        """~0.25 hp per in^3/min in aluminium (Machinery's Handbook unit power)."""
        r = compute(Cut("alu6061", diameter=12, flutes=3, ae=1.2, ap=12, max_rpm=8100))
        self.assertAlmostEqual(r.mrr, 1.2 * 12 * r.feed, places=6)
        hp_per_in3 = (r.power_kw / 0.7457) / (r.mrr / 16387.064)
        self.assertAlmostEqual(hp_per_in3, 0.25, delta=0.03)

    def test_hss_runs_slower_than_carbide(self):
        carbide = compute(Cut("alu6061", diameter=10, flutes=3, ae=5, ap=5))
        hss = compute(Cut("alu6061", diameter=10, flutes=3, ae=5, ap=5, is_carbide=False))
        self.assertAlmostEqual(hss.rpm / carbide.rpm, 0.4, places=6)


class TestClampsAndWarnings(unittest.TestCase):
    def test_rpm_clamp_reports_the_lost_surface_speed(self):
        r = compute(Cut("alu6061", diameter=6, flutes=3, ae=3, ap=6, max_rpm=8100))
        self.assertEqual(r.rpm, 8100)
        self.assertTrue(any("speed limited" in c for c in r.clamped))

    def test_feed_clamp_lowers_the_real_chip_load(self):
        r = compute(Cut("alu6061", diameter=12, flutes=3, ae=1.2, ap=12,
                        max_rpm=8100, max_feed=3000))
        self.assertEqual(r.feed, 3000)
        self.assertAlmostEqual(r.fz_commanded, 3000 / (3 * 8100), places=9)
        self.assertTrue(any("feed limited" in c for c in r.clamped))

    def test_slotting_is_flagged(self):
        r = compute(Cut("steel4140", diameter=10, flutes=4, ae=10, ap=10))
        self.assertTrue(any("slotting" in w for w in r.warnings))

    def test_deflection_grows_with_the_cube_of_stickout(self):
        short = compute(Cut("steel4140", diameter=10, flutes=4, ae=2, ap=10, stickout=20))
        long_ = compute(Cut("steel4140", diameter=10, flutes=4, ae=2, ap=10, stickout=40))
        self.assertAlmostEqual(long_.deflection / short.deflection, 8.0, places=3)

    def test_finishing_budget_is_tighter_than_roughing(self):
        kw = dict(material="ss304", diameter=6, flutes=4, ae=0.2, ap=8, stickout=25)
        rough = compute(Cut(**kw))
        fine = compute(Cut(**kw, finishing=True))
        self.assertFalse(any("deflection" in w for w in rough.warnings))
        self.assertTrue(any("deflection" in w for w in fine.warnings))

    def test_spindle_power_limit(self):
        r = compute(Cut("steel4140", diameter=20, flutes=4, ae=20, ap=20, max_power_kw=5.0))
        self.assertTrue(any("kW" in w for w in r.warnings))

    def test_unknown_material_names_the_alternatives(self):
        with self.assertRaises(KeyError) as ctx:
            compute(Cut("unobtainium", diameter=10, flutes=3, ae=5, ap=5))
        self.assertIn("alu6061", str(ctx.exception))

    def test_every_material_computes(self):
        for key in MATERIALS:
            with self.subTest(material=key):
                r = compute(Cut(key, diameter=10, flutes=3, ae=2, ap=10, stickout=30))
                self.assertGreater(r.rpm, 0)
                self.assertGreater(r.feed, 0)
                self.assertGreater(r.mrr, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
