from datetime import datetime
import unittest

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class OptimalWateringPlannerTests(unittest.TestCase):
    def test_full_16_hour_crop_reaches_lower_bound_with_three_future_waters(self):
        start = datetime(2026, 7, 25, 2, 51, 28)
        plan = app.plan_optimal_watering_schedule(
            start, remaining_min=880, cycle_min=960
        )

        self.assertEqual(plan["watering_count"], 3)
        self.assertAlmostEqual(plan["elapsed_min"], 704.0, places=6)
        self.assertEqual(
            [round(value, 6) for value in plan["intervals_min"]],
            [320.0, 320.0, 64.0],
        )
        self.assertTrue(plan["exact_maturity"])

    def test_current_ocr_remaining_time_does_not_use_full_cycle_plan(self):
        start = datetime(2026, 7, 25, 2, 51, 28)
        plan = app.plan_optimal_watering_schedule(
            start, remaining_min=435.52, cycle_min=960
        )

        self.assertEqual(plan["watering_count"], 2)
        self.assertAlmostEqual(plan["elapsed_min"], 348.416, places=6)
        self.assertAlmostEqual(plan["intervals_min"][0], 316.416, places=6)
        self.assertAlmostEqual(plan["intervals_min"][1], 32.0, places=6)

    def test_cooldown_boundary_uses_first_available_action(self):
        start = datetime(2026, 7, 25, 0, 0, 0)
        plan = app.plan_optimal_watering_schedule(
            start, remaining_min=35, cycle_min=960
        )
        self.assertEqual(plan["watering_count"], 1)
        self.assertAlmostEqual(plan["elapsed_min"], 32.0, places=6)
        self.assertFalse(plan["exact_maturity"])

    def test_natural_maturity_wins_when_remaining_is_before_cooldown(self):
        start = datetime(2026, 7, 25, 0, 0, 0)
        plan = app.plan_optimal_watering_schedule(
            start, remaining_min=30, cycle_min=960
        )
        self.assertEqual(plan["watering_count"], 0)
        self.assertEqual(plan["next_action"], "mature")
        self.assertAlmostEqual(plan["elapsed_min"], 30.0, places=6)

    def test_all_supported_cycles_use_the_declared_ratios(self):
        for cycle in (60, 480, 960, 1920):
            with self.subTest(cycle=cycle):
                plan = app.plan_optimal_watering_schedule(
                    datetime(2026, 7, 25),
                    remaining_min=cycle - cycle / 12,
                    cycle_min=cycle,
                )
                self.assertAlmostEqual(
                    plan["elapsed_min"],
                    0.8 * (cycle - cycle / 12),
                    places=6,
                )
                self.assertTrue(
                    all(
                        interval >= cycle / 30 - 1e-9
                        for interval in plan["intervals_min"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
