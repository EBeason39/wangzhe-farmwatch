import json
import unittest
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import DEFAULT, patch

import numpy as np

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
def ocr_line(x1, y1, x2, y2, text, score=0.99):
    return ([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], text, score)


class MaturityDateTests(unittest.TestCase):
    def test_parse_crop_name_text_normalizes_ocr_spacing(self):
        self.assertEqual(app.parse_crop_name_text("  西 瓜  "), "西瓜")
        self.assertIsNone(app.parse_crop_name_text("10:07成熟"))

    def test_mutant_crop_uses_base_crop_name(self):
        self.assertEqual(
            app.parse_crop_name_text("变异柠檬 0/3"), "柠檬"
        )
        self.assertEqual(app.normalize_crop_name("变异柠檬"), "柠檬")
        self.assertEqual(app.normalize_crop_name("柠檬（变异）"), "柠檬")

    def test_mutant_record_reuses_base_crop_cycle_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            path.write_text(
                '{"schema_version":3,"current_crop_name":"变异柠檬",'
                '"crops":{'
                '"柠檬":{"cycle_min":960,"updated_at":"2026-09-12 10:00:00"},'
                '"变异柠檬":{"cycle_min":60,"updated_at":"2026-09-12 21:00:00"}'
                '}}',
                encoding="utf-8",
            )
            with patch.object(app, "CYCLE_FILE", str(path)):
                selected = app.select_crop_cycle(
                    "变异柠檬", remaining_min=66.98
                )
                record = app.load_crop_record()
                stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(selected["crop_name"], "柠檬")
        self.assertEqual(selected["cycle_min"], 960)
        self.assertEqual(selected["source"], "record")
        self.assertEqual(record["crop_name"], "柠檬")
        self.assertEqual(record["cycle_min"], 960)
        self.assertNotIn("变异柠檬", stored["crops"])

    def test_dynamic_crop_name_uses_text_above_water_anchor(self):
        result = [
            ocr_line(40, 20, 120, 42, "猕猴桃"),
            ocr_line(174, 178, 218, 202, "0/3"),
            ocr_line(35, 214, 205, 238, "明天12:13成熟"),
        ]
        self.assertEqual(app.detect_crop_name_from_ocr(result), "猕猴桃")

    def test_dynamic_crop_name_accepts_all_water_levels(self):
        for water in ("0/3", "1 / 3", "2/3", "3 /3"):
            with self.subTest(water=water):
                result = [
                    ocr_line(40, 80, 120, 102, "火龙果"),
                    ocr_line(174, 178, 218, 202, water),
                ]
                self.assertEqual(
                    app.detect_crop_name_from_ocr(result), "火龙果"
                )

    def test_dynamic_crop_name_rejects_ui_words_and_bad_geometry(self):
        result = [
            ocr_line(40, 100, 110, 122, "满级"),
            ocr_line(174, 178, 218, 202, "0/3"),
            ocr_line(35, 214, 205, 238, "明天12:13成熟"),
        ]
        self.assertIsNone(app.detect_crop_name_from_ocr(result))

    def test_dynamic_crop_name_merges_split_same_row_fragments(self):
        result = [
            ocr_line(40, 80, 60, 102, "猕"),
            ocr_line(62, 81, 82, 103, "猴"),
            ocr_line(84, 80, 104, 102, "桃"),
            ocr_line(174, 178, 218, 202, "0/3"),
        ]
        self.assertEqual(app.detect_crop_name_from_ocr(result), "猕猴桃")

    def test_parse_maturity_text(self):
        cases = [
            ("明天20:16成熟", (20, 16, 1)),
            ("后天09:37成熟", (9, 37, 2)),
            ("今天20：16成熟", (20, 16, 0)),
            ("20:16成熟", (20, 16, None)),
            ("活动 12:30 明天20:16成熟", (20, 16, 1)),
            ("倒计时 01:23 20:16成熟", (20, 16, None)),
            ("25:16成熟 明天20:16成熟", (20, 16, 1)),
            ("明天25:16成熟", None),
            ("明天20:61成熟", None),
            ("明天25:61成熟", None),
            ("明天24:00成熟", None),
            ("明天23:60成熟", None),
            ("123:45成熟", None),
            ("20:160成熟", None),
            ("活动时间12:30 明天20:16成熟", (20, 16, 1)),
            ("明天 20:16 成熟", (20, 16, 1)),
            ("20:16成熟（明天）", (20, 16, 1)),
        ]

        parser = getattr(app, "parse_maturity_text", None)
        self.assertIsNotNone(parser, "app.parse_maturity_text is not implemented")

        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parser(text), expected)

    def test_parse_relative_maturity_duration(self):
        parsed = app.parse_maturity_text(
            "柠檬 0/3 14小时39分钟后成熟 可升级",
            datetime(2026, 9, 11, 9, 0),
        )
        self.assertEqual(parsed, (23, 39, 0))

    def test_parse_relative_maturity_duration_crosses_midnight(self):
        parsed = app.parse_maturity_text(
            "柠檬 0/3 14小时39分钟后成熟 可升级",
            datetime(2026, 9, 11, 12, 0),
        )
        self.assertEqual(parsed, (2, 39, 1))

    def test_current_crop_label_keeps_same_day_clock_semantics(self):
        parsed = app.parse_maturity_text("0/3 10:07成熟")

        self.assertEqual(parsed, (10, 7, None))
        self.assertEqual(
            app.resolve_maturity_datetime(
                datetime(2026, 7, 25, 2, 51, 28), parsed
            ),
            datetime(2026, 7, 25, 10, 7),
        )

    def test_read_maturity_time_combines_ocr_lines_before_parsing(self):
        fake_image = np.zeros((90, 90, 3), dtype=np.uint8)
        fake_ocr_result = [
            (None, "明天", None),
            (None, "20:16成熟", None),
        ]

        with (
            patch.object(app.cv2, "imread", return_value=fake_image),
            patch.object(
                app, "get_ocr", return_value=lambda _roi: (fake_ocr_result, None)
            ),
            redirect_stdout(StringIO()),
        ):
            result = app.read_maturity_time("ignored.png")

        self.assertEqual(result, ((20, 16, 1), False))

    def test_read_crop_state_returns_name_and_maturity_from_one_ocr_result(self):
        fake_image = np.zeros((90, 90, 3), dtype=np.uint8)
        fake_result = [
            (None, "西 瓜", None),
            (None, "10:07成熟", None),
        ]
        with (
            patch.object(app.cv2, "imread", return_value=fake_image),
            patch.object(
                app, "get_ocr", return_value=lambda _roi: (fake_result, None)
            ),
            redirect_stdout(StringIO()),
        ):
            state = app.read_crop_state("ignored.png")
        self.assertEqual(state["crop_name"], "西瓜")
        self.assertEqual(state["maturity_time"], (10, 7, None))
        self.assertFalse(state["is_mature"])

    def test_read_crop_state_accepts_unlisted_crop_name_from_geometry(self):
        fake_image = np.zeros((300, 300, 3), dtype=np.uint8)
        fake_result = [
            ocr_line(40, 20, 120, 42, "猕猴桃"),
            ocr_line(174, 178, 218, 202, "0/3"),
            ocr_line(35, 214, 205, 238, "明天12:13成熟"),
        ]
        with (
            patch.object(app.cv2, "imread", return_value=fake_image),
            patch.object(
                app, "get_ocr", return_value=lambda _roi: (fake_result, None)
            ),
            redirect_stdout(StringIO()),
        ):
            state = app.read_crop_state("ignored.png")
        self.assertEqual(state["crop_name"], "猕猴桃")
        self.assertEqual(state["maturity_time"], (12, 13, 1))

    def test_resolve_maturity_datetime(self):
        cases = [
            (
                datetime(2026, 7, 22, 15, 3, 7),
                (20, 16, 1),
                datetime(2026, 7, 23, 20, 16),
            ),
            (
                datetime(2026, 7, 22, 15, 3, 7),
                (9, 37, 2),
                datetime(2026, 7, 24, 9, 37),
            ),
            (
                datetime(2026, 7, 22, 15, 3, 7),
                (20, 16, None),
                datetime(2026, 7, 22, 20, 16),
            ),
            (
                datetime(2026, 7, 22, 23, 50),
                (0, 16, None),
                datetime(2026, 7, 23, 0, 16),
            ),
            (
                datetime(2026, 7, 22, 23, 50),
                (0, 16, 0),
                datetime(2026, 7, 22, 0, 16),
            ),
            (
                datetime(2026, 7, 22, 20, 16),
                (20, 16, None),
                datetime(2026, 7, 23, 20, 16),
            ),
        ]

        resolver = getattr(app, "resolve_maturity_datetime", None)
        self.assertIsNotNone(
            resolver, "app.resolve_maturity_datetime is not implemented"
        )

        for first_water_time, parsed_time, expected in cases:
            with self.subTest(first_water_time=first_water_time, parsed_time=parsed_time):
                self.assertEqual(
                    resolver(first_water_time, parsed_time),
                    expected,
                )

    def test_schedule_uses_ocr_remaining_time_and_earliest_plan(self):
        first_water_time = datetime(2026, 7, 25, 2, 51, 28)
        observed_maturity = datetime(2026, 7, 25, 10, 7)

        with patch.object(
            app, "load_crop_cycle", return_value=("作物", 960)
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time, observed_maturity
            )

        self.assertAlmostEqual(
            (result["water2"] - first_water_time).total_seconds() / 60,
            316.4266666667,
            places=3,
        )
        self.assertIsNotNone(result["water3"])
        self.assertLess(result["mature_time"], observed_maturity)
        self.assertEqual(result["mature_time"], result["water3"])


class MaturityDatetimeTests(unittest.TestCase):
    def setUp(self):
        app._pending_target_time = None

    def tearDown(self):
        # schedule 会记录目标时刻；留到后续 main() 测试会触发真实等待。
        app._pending_target_time = None

    def test_crop_observation_passes_resolved_datetime_to_cycle_calculation(self):
        first_water_time = datetime(2026, 7, 22, 15, 3, 7)
        parsed_time = (20, 16, 1)
        expected_maturity_dt = datetime(2026, 7, 23, 20, 16)
        sentinel = object()

        with (
            patch.object(app, "move_joystick"),
            patch.object(app.time, "sleep"),
            patch.object(app, "screenshot"),
            patch.object(
                app,
                "read_crop_state",
                return_value={
                    "crop_name": None,
                    "maturity_time": parsed_time,
                    "is_mature": False,
                },
            ),
            patch.object(
                app,
                "read_maturity_time",
                return_value=(parsed_time, False),
            ),
            patch.object(
                app,
                "calculate_plant_cycle_and_water_time",
                return_value=sentinel,
            ) as calculate_mock,
            redirect_stdout(StringIO()),
        ):
            result = app.inspect_crop(first_water_time)

        calculate_mock.assert_called_once_with(
            first_water_time,
            expected_maturity_dt,
            save_if_fresh=False,
        )
        self.assertEqual(result, (parsed_time, sentinel, expected_maturity_dt))

    def test_crop_observation_can_distinguish_unreadable_from_mature(self):
        first_water_time = datetime(2026, 7, 22, 15, 3, 7)

        with (
            patch.object(app, "move_joystick"),
            patch.object(app.time, "sleep"),
            patch.object(app, "screenshot"),
            patch.object(
                app,
                "read_crop_state",
                return_value={
                    "crop_name": None,
                    "maturity_time": None,
                    "is_mature": False,
                },
            ),
            patch.object(
                app, "read_maturity_time", return_value=(None, False)
            ),
            redirect_stdout(StringIO()),
        ):
            result = app.inspect_crop(
                first_water_time, return_status=True
            )

        self.assertEqual(result, (None, None, None, False))

    def test_crop_observation_passes_current_cycle_hint(self):
        first_water_time = datetime(2026, 7, 23, 1, 43, 7)
        parsed_time = (20, 16, 0)
        expected_maturity_dt = datetime(2026, 7, 23, 20, 16)
        sentinel = object()

        with (
            patch.object(app, "move_joystick"),
            patch.object(app.time, "sleep"),
            patch.object(app, "screenshot"),
            patch.object(
                app,
                "read_crop_state",
                return_value={
                    "crop_name": None,
                    "maturity_time": parsed_time,
                    "is_mature": False,
                },
            ),
            patch.object(
                app,
                "read_maturity_time",
                return_value=(parsed_time, False),
            ),
            patch.object(
                app,
                "calculate_plant_cycle_and_water_time",
                return_value=sentinel,
            ) as calculate_mock,
            redirect_stdout(StringIO()),
        ):
            result = app.inspect_crop(
                first_water_time, cycle_hint=1920
            )

        calculate_mock.assert_called_once_with(
            first_water_time,
            expected_maturity_dt,
            save_if_fresh=False,
            cycle_hint=1920,
        )
        self.assertEqual(result, (parsed_time, sentinel, expected_maturity_dt))

    def test_crop_observation_passes_crop_name_to_calculation(self):
        first_water_time = datetime(2026, 7, 25, 2, 51, 28)
        parsed_time = (10, 7, None)
        sentinel = object()

        with (
            patch.object(app, "move_joystick"),
            patch.object(app.time, "sleep"),
            patch.object(app, "screenshot"),
            patch.object(
                app,
                "read_crop_state",
                return_value={
                    "crop_name": "西瓜",
                    "maturity_time": parsed_time,
                    "is_mature": False,
                },
            ),
            patch.object(
                app,
                "calculate_plant_cycle_and_water_time",
                return_value=sentinel,
            ) as calculate_mock,
            redirect_stdout(StringIO()),
        ):
            result = app.inspect_crop(
                first_water_time, return_status=True
            )

        self.assertEqual(result[1], sentinel)
        self.assertEqual(calculate_mock.call_args.kwargs["crop_name"], "西瓜")

    def test_schedule_retries_when_maturity_is_unreadable(self):
        output = StringIO()
        with (
            patch.object(app, "adb_shell"),
            redirect_stdout(output),
        ):
            retry_time = app.schedule_next_round(
                None, None, None, is_mature=False
            )

        self.assertIsNotNone(retry_time)
        self.assertGreaterEqual(
            (retry_time - datetime.now()).total_seconds(), 295
        )
        self.assertIn("无法识别时间", output.getvalue())

    def test_schedule_prefers_next_watering_over_legacy_water2(self):
        next_time = datetime.now() + timedelta(minutes=20)
        legacy_time = next_time + timedelta(minutes=30)
        mature_time = next_time + timedelta(minutes=60)
        result = {
            "next_action": "water",
            "next_watering": next_time,
            "mature_time": mature_time,
            "water2": legacy_time,
        }
        with (
            patch.object(app, "adb_shell"),
            redirect_stdout(StringIO()),
        ):
            wake = app.schedule_next_round(
                (12, 0, None), result, mature_time, is_mature=False
            )
        self.assertLess(wake, next_time)

    def test_schedule_delays_final_harvest_watering_by_one_minute(self):
        final_watering = datetime.now() + timedelta(minutes=20)
        result = {
            "next_action": "water",
            "next_watering": final_watering,
            "mature_time": final_watering,
        }
        expected_target = final_watering + timedelta(seconds=60)

        with (
            patch.object(app, "WAKE_AHEAD_MIN", 5),
            patch.object(app, "adb_shell"),
            redirect_stdout(StringIO()),
        ):
            wake = app.schedule_next_round(
                (12, 0, None), result, final_watering, is_mature=False
            )

        self.assertEqual(app._pending_target_time, expected_target)
        self.assertEqual(wake, expected_target - timedelta(minutes=5))

    def test_schedule_delays_mature_harvest_target_by_one_minute(self):
        planned_maturity = datetime.now() + timedelta(minutes=20)
        observed_maturity = planned_maturity + timedelta(minutes=45)
        result = {
            "next_action": "mature",
            "next_watering": None,
            "mature_time": planned_maturity,
            "water2": observed_maturity,
        }
        expected_target = planned_maturity + timedelta(seconds=60)

        with (
            patch.object(app, "WAKE_AHEAD_MIN", 5),
            patch.object(app, "adb_shell"),
            redirect_stdout(StringIO()),
        ):
            wake = app.schedule_next_round(
                (12, 0, None), result, observed_maturity, is_mature=False
            )

        self.assertEqual(app._pending_target_time, expected_target)
        self.assertEqual(wake, expected_target - timedelta(minutes=5))

    def test_schedule_uses_planned_maturity_when_no_more_watering_is_needed(self):
        planned_maturity = datetime.now() + timedelta(minutes=20)
        observed_maturity = planned_maturity + timedelta(minutes=45)
        result = {
            "next_action": "mature",
            "next_watering": None,
            "mature_time": planned_maturity,
            "water2": observed_maturity,
        }
        with (
            patch.object(app, "adb_shell"),
            redirect_stdout(StringIO()),
        ):
            wake = app.schedule_next_round(
                (12, 0, None), result, observed_maturity, is_mature=False
            )
        self.assertLess(wake, planned_maturity)
        self.assertGreater(wake, datetime.now())


class MainCycleHintTests(unittest.TestCase):
    class StopMain(RuntimeError):
        pass

    def setUp(self):
        app._pending_target_time = None

    def tearDown(self):
        app._pending_target_time = None

    def run_main_until_crop_observation_stops(self, harvest_results, crop_observation_results):
        with (
            patch.object(app, "ensure_emulator_running", return_value=True),
            patch.object(app, "check_adb_connection", return_value=True),
            patch.object(app, "detect_resolution", return_value=(1280, 720)),
            patch.object(app, "report_device_state"),
            patch.object(app, "launch_game"),
            patch.object(app, "wait_for_game_update", return_value=True),
            patch.object(app, "dismiss_startup_overlays"),
            patch.object(app, "ensure_account_ready", return_value=True),
            patch.object(app, "enter_game", return_value=True),
            patch.object(app, "dismiss_lobby_overlays"),
            patch.object(app, "open_farm", return_value=True),
            patch.object(app, "approach_farm_control"),
            patch.object(app, "perform_farm_action", return_value=True),
            patch.object(app, "clear_crop_cycle") as clear_cycle_mock,
            patch.object(
                app, "collect_harvest_result", side_effect=harvest_results
            ),
            patch.object(
                app, "inspect_crop", side_effect=crop_observation_results
            ) as crop_observation_mock,
            patch.object(
                app,
                "schedule_next_round",
                return_value=datetime.min,
            ),
            patch.multiple(app, wake_screen=DEFAULT,
                           visit_pinned_friends=DEFAULT, _exit_game_for_wait=DEFAULT),
            redirect_stdout(StringIO()),
        ):
            with self.assertRaises(self.StopMain):
                app.main()

        return crop_observation_mock.call_args_list, clear_cycle_mock.call_count

    def test_run_main_helper_does_not_call_real_login_gate(self):
        """The lifecycle helper must isolate the login gate like other steps."""
        with patch.object(
            app,
            "ensure_account_ready",
            side_effect=self.StopMain(),
        ) as real_gate:
            self.run_main_until_crop_observation_stops(
                harvest_results=[(False, False)],
                crop_observation_results=[self.StopMain()],
            )

        real_gate.assert_not_called()

    def test_main_passes_confirmed_cycle_hint_to_next_round(self):
        maturity_dt = datetime(2026, 7, 23, 20, 16)
        schedule = {
            "plant_cycle_min": 1920,
            "water2": maturity_dt,
            "water3": maturity_dt,
            "mature_time": maturity_dt,
        }

        calls, _ = self.run_main_until_crop_observation_stops(
            harvest_results=[(False, False), (False, False)],
            crop_observation_results=[
                ((20, 16, 0), schedule, maturity_dt, False),
                self.StopMain(),
            ],
        )

        self.assertEqual(calls[1].kwargs.get("cycle_hint"), 1920)

    def test_main_clears_cycle_hint_before_crop_observation_after_harvest(self):
        maturity_dt = datetime(2026, 7, 23, 20, 16)
        schedule = {
            "plant_cycle_min": 1920,
            "water2": maturity_dt,
            "water3": maturity_dt,
            "mature_time": maturity_dt,
        }

        calls, _ = self.run_main_until_crop_observation_stops(
            harvest_results=[
                (False, False),
                (False, False),
                (True, True),
            ],
            crop_observation_results=[
                ((20, 16, 0), schedule, maturity_dt, False),
                ((20, 16, 0), schedule, maturity_dt, False),
                self.StopMain(),
            ],
        )

        self.assertEqual(calls[1].kwargs.get("cycle_hint"), 1920)
        self.assertIsNone(calls[2].kwargs.get("cycle_hint"))

    def test_main_does_not_count_later_watering_after_first_ocr_failure(self):
        maturity_dt = datetime(2026, 7, 23, 20, 16)
        schedule = {
            "plant_cycle_min": 1920,
            "water2": maturity_dt,
            "water3": maturity_dt,
            "mature_time": maturity_dt,
        }

        calls, clear_count = self.run_main_until_crop_observation_stops(
            harvest_results=[
                (True, True),
                (False, False),
                (False, False),
            ],
            crop_observation_results=[
                (None, None, None, False),
                ((20, 16, 0), schedule, maturity_dt, False),
                self.StopMain(),
            ],
        )

        self.assertTrue(calls[0].kwargs["save_if_fresh"])
        self.assertIsNone(calls[0].kwargs.get("cycle_hint"))
        self.assertFalse(calls[1].kwargs["save_if_fresh"])
        self.assertIsNone(calls[1].kwargs.get("cycle_hint"))
        self.assertFalse(calls[2].kwargs["save_if_fresh"])
        self.assertEqual(calls[2].kwargs.get("cycle_hint"), 1920)
        self.assertEqual(clear_count, 1)


class PlantCycleTests(unittest.TestCase):
    def test_very_large_integer_cycle_hint_logging_is_safe(self):
        first_water_time = datetime(2026, 7, 22, 15, 3)
        mature_time = datetime(2026, 7, 22, 21, 43)
        huge_cycle = 10**5000
        output = StringIO()

        with (
            patch.object(
                app, "load_crop_cycle", return_value=(None, None)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(output),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time,
                mature_time,
                cycle_hint=huge_cycle,
            )

        load_mock.assert_called_once_with()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 480)
        self.assertIn("格式无效", output.getvalue())

    def test_very_large_integer_stored_cycle_logging_is_safe(self):
        first_water_time = datetime(2026, 7, 22, 15, 3)
        mature_time = datetime(2026, 7, 22, 15, 58)
        huge_cycle = 10**5000
        output = StringIO()

        with (
            patch.object(
                app, "load_crop_cycle", return_value=("作物", huge_cycle)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(output),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time,
                mature_time,
            )

        load_mock.assert_called_once_with()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 60)
        self.assertIn("格式无效", output.getvalue())

    def test_huge_integer_cycle_hint_is_ignored(self):
        first_water_time = datetime(2026, 7, 22, 15, 3)
        mature_time = datetime(2026, 7, 22, 21, 43)

        with (
            patch.object(
                app, "load_crop_cycle", return_value=(None, None)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(StringIO()),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time,
                mature_time,
                cycle_hint=10**1000,
            )

        load_mock.assert_called_once_with()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 480)

    def test_huge_float_stored_cycle_is_ignored(self):
        first_water_time = datetime(2026, 7, 22, 15, 3)
        mature_time = datetime(2026, 7, 22, 15, 58)

        with (
            patch.object(
                app, "load_crop_cycle", return_value=("作物", 1e308)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(StringIO()),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time,
                mature_time,
            )

        load_mock.assert_called_once_with()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 60)

    def test_fresh_cycle_skips_old_hint_and_persisted_cycle(self):
        first_water_time = datetime(2026, 7, 22, 15, 3)
        mature_time = datetime(2026, 7, 22, 15, 58)

        with (
            patch.object(
                app,
                "load_crop_cycle",
                side_effect=AssertionError("fresh cycle must not load old state"),
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(StringIO()),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time,
                mature_time,
                save_if_fresh=True,
                cycle_hint=10**1000,
            )

        load_mock.assert_not_called()
        save_mock.assert_called_once_with("作物", 60)
        self.assertEqual(result["plant_cycle_min"], 60)

    def test_stale_stored_cycle_is_replaced_when_maturity_is_next_day(self):
        first_water_time = datetime(2026, 7, 22, 15, 3, 7)
        mature_time = datetime(2026, 7, 23, 20, 16)

        output = StringIO()
        with (
            patch.object(
                app, "load_crop_cycle", return_value=("作物", 60)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(output),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time, mature_time
            )

        load_mock.assert_called_once_with()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 1920)

    def test_matching_stored_cycle_is_preserved(self):
        first_water_time = datetime(2026, 7, 22, 15, 3)
        mature_time = datetime(2026, 7, 22, 21, 43)

        output = StringIO()
        with (
            patch.object(
                app, "load_crop_cycle", return_value=("作物", 960)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(output),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time, mature_time
            )

        load_mock.assert_called_once_with()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 960)
        self.assertIn("使用存储的作物周期", output.getvalue())

    def test_non_numeric_stored_cycle_is_ignored(self):
        first_water_time = datetime(2026, 7, 22, 15, 3, 7)
        mature_time = datetime(2026, 7, 23, 20, 16)

        output = StringIO()
        with (
            patch.object(
                app, "load_crop_cycle", return_value=("作物", "60")
            ),
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(output),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time, mature_time
            )

        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 1920)
        self.assertIn("格式无效", output.getvalue())

    def test_cycle_hint_prevents_later_watering_from_drifting_cycle(self):
        first_water_time = datetime(2026, 7, 23, 1, 43, 7)
        mature_time = datetime(2026, 7, 23, 20, 16)

        output = StringIO()
        with (
            patch.object(
                app, "load_crop_cycle", return_value=("作物", 60)
            ) as load_mock,
            patch.object(app, "save_crop_cycle") as save_mock,
            redirect_stdout(output),
        ):
            result = app.calculate_plant_cycle_and_water_time(
                first_water_time,
                mature_time,
                cycle_hint=1920,
            )

        load_mock.assert_not_called()
        save_mock.assert_not_called()
        self.assertEqual(result["plant_cycle_min"], 1920)
        self.assertIn("本轮缓存", output.getvalue())


class CropRecordTests(unittest.TestCase):
    def test_same_crop_with_shorter_remaining_keeps_recorded_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            with patch.object(app, "CYCLE_FILE", str(path)):
                app.save_crop_record(
                    crop_name="西瓜",
                    cycle_min=960,
                    planted_at=datetime(2026, 7, 25),
                )
                selected = app.select_crop_cycle(
                    "西瓜", remaining_min=435.52
                )
        self.assertEqual(selected["cycle_min"], 960)
        self.assertFalse(selected["updated"])

    def test_same_crop_later_watering_never_changes_learned_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            with patch.object(app, "CYCLE_FILE", str(path)):
                app.save_crop_record(
                    crop_name="西瓜",
                    cycle_min=960,
                    planted_at=datetime(2026, 7, 25),
                )
                selected = app.select_crop_cycle(
                    "西瓜", remaining_min=1753
                )
        self.assertEqual(selected["cycle_min"], 960)
        self.assertFalse(selected["updated"])

    def test_different_name_does_not_reuse_old_crop_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            with patch.object(app, "CYCLE_FILE", str(path)):
                app.save_crop_record(
                    crop_name="西瓜",
                    cycle_min=960,
                    planted_at=datetime(2026, 7, 25),
                )
                selected = app.select_crop_cycle(
                    "番茄", remaining_min=435
                )
        self.assertEqual(selected["cycle_min"], 480)
        self.assertTrue(selected["updated"])

    def test_unreadable_name_keeps_valid_record_without_overwriting_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            with patch.object(app, "CYCLE_FILE", str(path)):
                app.save_crop_record(
                    crop_name="西瓜",
                    cycle_min=960,
                    planted_at=datetime(2026, 7, 25),
                )
                selected = app.select_crop_cycle(
                    None, remaining_min=435
                )
                record = app.load_crop_record()
        self.assertEqual(selected["cycle_min"], 960)
        self.assertEqual(record["crop_name"], "西瓜")

    def test_old_record_is_read_and_exposed_as_current_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            path.write_text(
                '{"crop_name":"西瓜","cycle_min":960,'
                '"update_time":"2026-07-24 20:46:26"}',
                encoding="utf-8",
            )
            with patch.object(app, "CYCLE_FILE", str(path)):
                record = app.load_crop_record()
        self.assertEqual(record["schema_version"], 3)
        self.assertAlmostEqual(record["initial_remaining_min"], 880.0)

    def test_schema_two_migrates_and_preserves_records_by_crop_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            path.write_text(
                '{"schema_version":2,"crop_name":"西瓜","cycle_min":960,'
                '"updated_at":"2026-07-24 20:46:26"}',
                encoding="utf-8",
            )
            with patch.object(app, "CYCLE_FILE", str(path)):
                app.save_crop_record("辣椒", 1920, last_remaining_min=1760)
                watermelon = app.load_crop_record("西瓜")
                pepper = app.load_crop_record("辣椒")
                stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(watermelon["cycle_min"], 960)
        self.assertEqual(pepper["cycle_min"], 1920)
        self.assertEqual(stored["schema_version"], 3)
        self.assertEqual(stored["current_crop_name"], "辣椒")
        self.assertEqual(set(stored["crops"]), {"西瓜", "辣椒"})

    def test_clearing_current_batch_preserves_known_crop_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            with patch.object(app, "CYCLE_FILE", str(path)):
                app.save_crop_record("西瓜", 960)
                app.save_crop_record("辣椒", 1920)
                app.clear_crop_cycle()
                watermelon = app.load_crop_record("西瓜")
                pepper = app.load_crop_record("辣椒")
                current = app.load_crop_record()

        self.assertEqual(watermelon["cycle_min"], 960)
        self.assertEqual(pepper["cycle_min"], 1920)
        self.assertIsNone(current)

    def test_generic_legacy_name_is_upgraded_without_downgrading_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            path.write_text(
                '{"crop_name":"作物","cycle_min":960,'
                '"update_time":"2026-07-24 20:46:26"}',
                encoding="utf-8",
            )
            with patch.object(app, "CYCLE_FILE", str(path)):
                selected = app.select_crop_cycle(
                    "西瓜", remaining_min=435.52
                )
                record = app.load_crop_record()

        self.assertEqual(selected["cycle_min"], 960)
        self.assertEqual(selected["source"], "record-name-upgraded")
        self.assertEqual(record["crop_name"], "西瓜")
        self.assertEqual(record["cycle_min"], 960)

    def test_unsupported_five_minute_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            path.write_text(
                '{"crop_name":"西瓜","cycle_min":5}', encoding="utf-8"
            )
            with patch.object(app, "CYCLE_FILE", str(path)):
                self.assertIsNone(app.load_crop_record())


class OptimalCalculationIntegrationTests(unittest.TestCase):
    def test_calculation_returns_optimal_plan_and_compatibility_fields(self):
        first = datetime(2026, 7, 25, 2, 51, 28)
        observed = datetime(2026, 7, 25, 10, 7)
        with patch.object(
            app,
            "load_crop_record",
            return_value={"crop_name": "西瓜", "cycle_min": 960},
        ), patch.object(app, "save_crop_record"):
            result = app.calculate_plant_cycle_and_water_time(
                first, observed, crop_name="西瓜"
            )
        self.assertEqual(result["plant_cycle_min"], 960)
        self.assertEqual(result["water_maintenance_min"], 320)
        self.assertEqual(result["water_cooldown_min"], 32)
        self.assertEqual(result["next_action"], "water")
        self.assertIs(result["next_watering"], result["watering_times"][0])
        self.assertIn("water2", result)
        self.assertIn("water3", result)
        self.assertIn("water4", result)

    def test_corrupt_record_is_ignored_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.json"
            path.write_text("not-json", encoding="utf-8")
            with patch.object(app, "CYCLE_FILE", str(path)):
                self.assertIsNone(app.load_crop_record())


if __name__ == "__main__":
    unittest.main()
