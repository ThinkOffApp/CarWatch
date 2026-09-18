"""Issue #39: the car agent told petrus his tyres were at THIRTY BAR.

Root cause: Home Assistant reports tyre pressure in whatever unit the owner's
HA is configured for. The fetch read `attributes.unit_of_measurement` and then
threw it away, filing the value under `tires_kpa` regardless, and the prompt
handed the model a bare number plus a unit word to convert for itself.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carwatch.mercedesme import (  # noqa: E402
    _norm_value, _pressure_kpa, fmt_tyres_bar,
)


class TyrePressureUnits(unittest.TestCase):

    def test_converts_from_every_unit_ha_might_report(self):
        self.assertEqual(_pressure_kpa(2.4, "bar"), 240.0)
        self.assertEqual(_pressure_kpa(240, "kPa"), 240.0)
        self.assertEqual(round(_pressure_kpa(35, "psi")), 241)
        self.assertEqual(_pressure_kpa(2400, "mbar"), 240.0)

    def test_unknown_unit_refuses_rather_than_assuming_kpa(self):
        # The old code assumed kPa, which is how a psi reading became a "kPa"
        # value. An unlabelled pressure must not be invented.
        self.assertIsNone(_pressure_kpa(30, None))
        self.assertIsNone(_pressure_kpa(30, ""))
        self.assertIsNone(_pressure_kpa(30, "furlongs"))

    def test_psi_entity_no_longer_becomes_ten_times_too_large(self):
        kpa = _norm_value("sensor.eclass_tirepressure_front_left", "30",
                          "psi", to_kpa=True)
        bar = kpa / 100.0
        self.assertTrue(1.9 < bar < 2.2, f"30 psi should be ~2.1 bar, got {bar}")
        self.assertIn("2.1 bar", fmt_tyres_bar({"front_left": kpa}))

    def test_output_is_bar_one_decimal_and_model_never_converts(self):
        out = fmt_tyres_bar({"front_left": 250, "front_right": 250,
                             "rear_left": 260, "rear_right": 260})
        self.assertEqual(out, "front left 2.5 bar, front right 2.5 bar, "
                              "rear left 2.6 bar, rear right 2.6 bar")
        self.assertNotIn("kPa", out)

    def test_implausible_pressure_is_flagged_not_stated(self):
        self.assertIn("IMPLAUSIBLE", fmt_tyres_bar({"front_left": 3000}))
        self.assertIn("IMPLAUSIBLE", fmt_tyres_bar({"front_left": 30}))

    def test_wheels_ordered_and_missing_values_skipped(self):
        out = fmt_tyres_bar({"rear_right": 260, "front_left": 250,
                             "front_right": None})
        self.assertTrue(out.startswith("front left"))
        self.assertNotIn("front right", out)

    def test_non_pressure_entities_are_untouched(self):
        self.assertEqual(_norm_value("lock.eclass", "locked"), "locked")
        self.assertEqual(_norm_value("sensor.eclass_odometer", "48211"), 48211.0)


if __name__ == "__main__":
    unittest.main()
