import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from farmwatch.automation import friends as f
from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
w = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
repaired = w


class DailyLimitTests(unittest.TestCase):
    def test_night_window_uses_china_time_and_opens_at_eight(self):
        expected = datetime(2026, 11, 11, 8, tzinfo=f.CHINA_TZ)
        self.assertEqual(f.take_blocked_until(datetime(2026, 11, 11)), expected)
        self.assertEqual(f.take_blocked_until(datetime(2026, 11, 11, 7, 59, 59)), expected)
        self.assertEqual(f.take_blocked_until(datetime(2026, 11, 10, 23, 59, tzinfo=timezone.utc)), expected)
        self.assertIsNone(f.take_blocked_until(datetime(2026, 11, 11, 8)))
        self.assertIsNone(f.take_blocked_until(datetime(2026, 11, 11, 23, 59)))

    def test_china_0800_boundary_including_utc_input(self):
        self.assertEqual(f.game_day(datetime(2026, 11, 10, 7, 59, 59)), "2026-11-09")
        self.assertEqual(f.game_day(datetime(2026, 11, 10, 8)), "2026-11-10")
        self.assertEqual(f.game_day(datetime(2026, 11, 10, tzinfo=timezone.utc)), "2026-11-10")

    def test_exhaustion_survives_restart_and_resets_at_eight(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            now = datetime(2026, 11, 10, 21)
            limit = f.DailyLimit(path, "farm", now)
            limit.observe(0, now=now)
            restored = f.DailyLimit(path, "farm", datetime(2026, 11, 11, 7, 59))
            self.assertTrue(restored.exhausted)
            self.assertFalse(f.DailyLimit(path, "other", now).exhausted)
            restored.refresh(datetime(2026, 11, 11, 8))
            self.assertFalse(restored.exhausted)
            self.assertIsNone(restored.data["remaining"])

    def test_positive_reading_corrects_zero_without_guessing_daily_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            limit = f.DailyLimit(Path(tmp) / "state.json", "farm")
            limit.observe(0)
            limit.observe(35)
            self.assertFalse(limit.exhausted)
            self.assertEqual(limit.data["remaining"], 35)
            limit.observe(None)
            self.assertEqual(limit.data["remaining"], 35)

    def test_corrupt_state_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("broken", encoding="utf-8")
            limit = f.DailyLimit(path, "farm")
            self.assertFalse(limit.exhausted)
            limit.observe(12)
            self.assertEqual(json.loads(path.read_text())["farm"]["remaining"], 12)

    def test_feedback_and_separate_bag_count(self):
        self.assertIsNone(f.parse_remaining("剩余10"))
        self.assertEqual(f.parse_remaining("剩余 39 次"), 39)
        self.assertEqual(f.parse_remaining("剩余0次"), 0)
        self.assertEqual(f.parse_feedback("你已经拿取过了"), "unavailable")
        self.assertEqual(f.parse_feedback("今日拿取次数已达上限"), "exhausted")
        self.assertEqual(f.parse_feedback("拿取次数已用完"), "exhausted")
        self.assertNotEqual(f.parse_feedback("无法拿取"), "exhausted")
        self.assertEqual(f.parse_feedback("0:00-8：00无法拿取作物"), "night_closed")
        self.assertEqual(f.parse_feedback("00:00—08:00不能拿取作物"), "night_closed")

    def test_route_rejects_the_old_three_column_four_row_layout(self):
        route = f.load_route()
        route["stops"][3]["id"] = "L4-1"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route.json"
            path.write_text(json.dumps(route), encoding="utf-8")
            with self.assertRaises(ValueError):
                f.load_route(path)


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision = f.Vision(w.TEMPLATE_DIR, w.get_ocr())
        cls.fixtures = Path(__file__).parent / "fixtures" / "friends"

    def frame(self, name):
        return cv2.imread(str(self.fixtures / (name + ".png")))

    def test_yellow_rows_and_badges_are_independent(self):
        rows = self.vision.friends(self.frame("social"))
        self.assertEqual([r.pinned for r in rows], [True, True, True, False, False])
        self.assertEqual([r.available for r in rows], [False, False, False, True, True])
        self.assertEqual([r.name for r in rows[:3]], ["alpha", "bravo", "charlie"])
        for i, a in enumerate(rows[:3]):
            for j, b in enumerate(rows[:3]):
                self.assertEqual(self.vision.same_friend(a, b), i == j)

    def test_lit_and_dim_hands_on_recorded_frames(self):
        enabled = self.vision.plot(self.frame("enabled"))
        disabled = self.vision.plot(self.frame("disabled"))
        self.assertTrue(enabled.selected)
        self.assertIsNotNone(enabled.button)
        self.assertEqual(enabled.remaining, 38)
        self.assertEqual(enabled.stock, 44)
        self.assertFalse(enabled.disabled)
        self.assertIsNone(disabled.button)
        self.assertTrue(disabled.disabled)
        self.assertEqual(disabled.remaining, 39)

    def test_unripe_plot_is_resolved_without_clicking(self):
        state = self.vision.plot(self.frame("unripe"))
        self.assertTrue(state.selected)
        self.assertEqual(state.feedback, "unavailable")
        self.assertTrue(state.unripe)
        self.assertIsNone(state.remaining)
        self.assertIsNone(state.button)

    def test_live_night_message_preserves_the_displayed_daily_count(self):
        state = self.vision.plot(self.frame("night_closed"))
        self.assertEqual(state.feedback, "night_closed")
        self.assertEqual(state.remaining, 35)
        self.assertTrue(state.selected)

    def test_live_stock_and_translucent_hand_after_real_takes(self):
        for crop, counts in (("lemon", (60, 53)), ("pear", (28, 25)), ("orange", (90, 81)),
                             ("foliage", (60, 55)), ("bright_background", (27, 23)),
                             ("road", (40, 32))):
            with self.subTest(crop=crop):
                before = self.vision.plot(self.frame(f"live_{crop}_before"))
                after = self.vision.plot(self.frame(f"live_{crop}_after"))
                self.assertEqual((before.stock, after.stock), counts)
                self.assertTrue(before.selected and after.selected)
                self.assertIsNotNone(before.button)
                self.assertIsNone(after.button)
                self.assertTrue(after.disabled)
                self.assertIsNone(before.remaining)
                self.assertIsNone(after.remaining)

    def test_weaker_hand_match_cannot_enable_clicking(self):
        self.assertEqual(self.vision.take_button(self.frame("live_no_hand")), (None, False))
        with patch.object(cv2, "minMaxLoc", return_value=(0, .70, (0, 0), (45, 21))):
            button, disabled = self.vision.take_button(self.frame("live_lemon_before"))
        self.assertIsNone(button)
        self.assertFalse(disabled)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        # All action-path cases run during the allowed game window by default.
        night_patch = patch.object(f, "take_blocked_until", return_value=None)
        night_patch.start()
        self.addCleanup(night_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = SimpleNamespace(
            datetime=datetime, TEMPLATE_DIR=w.TEMPLATE_DIR, DATA_DIR=Path(self.tmp.name),
            MUMU_NAME="test", SCREENSHOT_PATH=str(Path(self.tmp.name) / "screen.png"),
            get_ocr=Mock(return_value=Mock()), screenshot=Mock(),
            adb_shell_result=Mock(return_value=subprocess.CompletedProcess([], 0)),
        )
        self.runner = f.Runner(self.host, datetime.now() + timedelta(minutes=20))
        self.runner.capture = Mock(return_value=np.zeros((720, 1280, 3), np.uint8))
        self.runner.vision = Mock()
        self.runner.tap = Mock()
        self.sleep = patch.object(f.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_success_requires_counter_change(self):
        before = f.PlotState(selected=True, remaining=39, stock=44, button=(1073, 569))
        after = f.PlotState(selected=True, remaining=38, stock=41, disabled=True)
        self.runner.vision.plot.side_effect = [before, after]
        self.assertEqual(self.runner.take_plot("L1-1"), "taken")
        self.runner.tap.assert_called_once_with(1073, 569)
        self.assertEqual(self.runner.limit.data["remaining"], 38)

    def test_unavailable_without_a_take_button_is_not_unknown(self):
        self.runner.vision.plot.return_value = f.PlotState(selected=True, feedback="unavailable", unripe=True)
        self.assertEqual(self.runner.take_plot("L1-1"), "unavailable")
        self.runner.tap.assert_not_called()

    def test_hidden_daily_counter_still_requires_a_confirmed_stock_change(self):
        self.runner.vision.plot.side_effect = [
            f.PlotState(selected=True, stock=44, button=(1073, 569)),
            f.PlotState(selected=True, stock=41, disabled=True),
        ]
        self.assertEqual(self.runner.take_plot("L1-1"), "taken")
        self.assertIsNone(self.runner.limit.data["remaining"])

    def test_previous_plot_toast_does_not_skip_an_enabled_plot(self):
        # These are the recorded frame-18 values after moving to the next plot.
        self.runner.vision.plot.side_effect = [
            f.PlotState(selected=True, remaining=37, stock=42, button=(1073, 569), feedback="unavailable"),
            f.PlotState(selected=True, remaining=36, stock=39, disabled=True, feedback="unavailable"),
        ]
        self.assertEqual(self.runner.take_plot("L1-1"), "taken")
        self.runner.tap.assert_called_once_with(1073, 569)

    def test_unchanged_success_toast_does_not_count_as_new_success(self):
        self.runner.vision.plot.return_value = f.PlotState(selected=True, button=(1073, 569), feedback="taken")
        self.assertEqual(self.runner.take_plot("L1-1"), "unknown")

    def test_click_without_feedback_never_counts_as_success(self):
        self.runner.vision.plot.return_value = f.PlotState(selected=True, button=(1073, 569))
        self.assertEqual(self.runner.take_plot("L1-1"), "unknown")
        self.assertEqual(self.runner.tap.call_count, 2)

    def test_daily_zero_requires_two_frames_and_stops_before_tapping(self):
        self.runner.vision.plot.return_value = f.PlotState(selected=True, remaining=0)
        with self.assertRaises(f.StopVisit):
            self.runner.take_plot("L1-1")
        self.assertTrue(self.runner.limit.exhausted)
        self.runner.tap.assert_not_called()
        self.assertEqual(self.runner.vision.plot.call_count, 2)

    def test_unconfirmed_zero_does_not_persist_exhaustion(self):
        self.runner.vision.plot.side_effect = [f.PlotState(selected=True, remaining=0), f.PlotState(selected=True, remaining=30)]
        with self.assertRaises(f.StopVisit):
            self.runner.take_plot("L1-1")
        self.assertFalse(self.runner.limit.exhausted)
        self.runner.tap.assert_not_called()

    def test_last_remaining_take_is_recorded_before_stopping(self):
        after = f.PlotState(selected=True, remaining=0, stock=41, disabled=True)
        self.runner.vision.plot.side_effect = [
            f.PlotState(selected=True, remaining=1, stock=44, button=(1073, 569)),
            after, after,
        ]
        self.assertEqual(self.runner.take_plot("L1-1"), "taken")
        self.assertTrue(self.runner.limit.exhausted)
        with self.assertRaises(f.StopVisit):
            self.runner.move([[180, 1100]])
        self.host.adb_shell_result.assert_not_called()

    def test_time_guard_prevents_adb_input(self):
        self.runner.deadline = f.time.monotonic() - 1
        with self.assertRaises(f.StopVisit):
            self.runner.move([[90, 500]])
        self.host.adb_shell_result.assert_not_called()

    def test_night_guard_skips_navigation_without_spending_the_daily_count(self):
        self.runner.limit.observe(35)
        self.runner.home = Mock()
        with patch.object(f, "take_blocked_until", return_value=datetime(2026, 11, 11, 8, tzinfo=f.CHINA_TZ)):
            report = self.runner.run()
        self.runner.home.assert_not_called()
        self.runner.tap.assert_not_called()
        self.assertFalse(self.runner.limit.exhausted)
        self.assertEqual(self.runner.limit.data["remaining"], 35)
        self.assertEqual(report["blocked_until"], "2026-11-11T08:00:00+08:00")

    def test_server_night_message_stops_even_if_local_clock_says_daytime(self):
        self.runner.vision.plot.return_value = f.PlotState(selected=True, remaining=35, feedback="night_closed")
        with self.assertRaises(f.StopVisit):
            self.runner.take_plot("L1-1")
        self.runner.tap.assert_not_called()
        self.assertFalse(self.runner.limit.exhausted)

    def test_less_than_one_minute_skips_before_opening_ui(self):
        with patch.object(f, "Runner") as runner:
            self.assertIsNone(f.run(self.host, datetime.now() + timedelta(seconds=30)))
        runner.assert_not_called()

    def test_exhaustion_skips_all_navigation_and_writes_a_report(self):
        self.runner.limit.observe(0)
        self.runner.home = Mock()
        self.runner.next_friend = Mock()
        report = self.runner.run()
        self.runner.home.assert_not_called()
        self.runner.next_friend.assert_not_called()
        self.assertIn("次数已用完", report["status"])
        self.assertEqual(report["outcomes"]["taken"], 0)
        self.assertEqual(json.loads((self.host.DATA_DIR / "friend_visits_last_run.json").read_text(encoding="utf-8")), report)

    def test_walk_does_not_mark_unknown_route_complete(self):
        self.runner.move = Mock()
        self.runner.take_plot = Mock(side_effect=["unavailable", "unknown"])
        self.runner.walk()
        visit = self.runner.report["visits"][0]
        self.assertFalse(visit["complete"])
        self.assertEqual(len(visit["plots"]), 2)

    def test_walk_checks_all_distinct_plots(self):
        self.runner.move = Mock()
        self.runner.take_plot = Mock(return_value="unavailable")
        self.runner.walk()
        visit = self.runner.report["visits"][0]
        self.assertTrue(visit["complete"])
        self.assertEqual(len({p["id"] for p in visit["plots"]}), 24)

    def test_dry_run_does_not_click_or_persist_game_counters(self):
        self.runner.dry_run = True
        self.runner.vision.plot.return_value = f.PlotState(selected=True, remaining=0, button=(1073, 569))
        self.assertEqual(self.runner.take_plot("L1-1"), "observed")
        self.runner.tap.assert_not_called()
        self.assertFalse(self.runner.limit.path.exists())

    def test_selection_requires_pin_badge_and_fresh_recheck(self):
        identity = np.zeros((36, 252), np.uint8)
        friends = [f.Friend(127, True, False, identity, "a"),
                   f.Friend(233, False, True, identity, "b"),
                   f.Friend(339, True, True, identity, "c")]
        self.runner.open_social = Mock()
        self.runner.vision.same_friend.side_effect = lambda a, b: a.name == b.name
        self.runner.vision.friends.side_effect = [friends, friends]
        self.assertEqual(self.runner.next_friend().name, "c")
        self.runner.vision.friends.side_effect = [friends, [f.Friend(339, False, True, identity, "c")], []]
        self.assertIsNone(self.runner.next_friend())


class MainFlowTests(unittest.TestCase):
    def run_round(self, app, *, enabled="1", error=None, plan=True, mature=False):
        class Done(Exception):
            pass
        events = []
        wake = datetime.now() + timedelta(hours=2)
        result = {"next_action": "water"} if plan else None
        with ExitStack() as stack, redirect_stdout(StringIO()):
            stack.enter_context(patch.object(app, "settings", replace(app.settings, friends_enabled=enabled == "1")))
            returns = {"ensure_emulator_running": True, "check_adb_connection": True,
                       "detect_resolution": (1280, 720), "launch_game": True,
                       "wait_for_game_update": True, "ensure_account_ready": True,
                       "enter_game": True, "open_farm": True,
                       "approach_farm_control": True, "perform_farm_action": True,
                       "collect_harvest_result": (False, False),
                       "inspect_crop": (None, result, wake, mature)}
            for name, value in returns.items():
                stack.enter_context(patch.object(app, name, return_value=value))
            for name in ("wake_screen", "report_device_state",
                         "dismiss_startup_overlays", "dismiss_lobby_overlays", "wait_until_target_time", "notify_round"):
                stack.enter_context(patch.object(app, name))
            calculate = stack.enter_context(patch.object(app, "schedule_next_round", side_effect=lambda *a, **k: events.append("schedule") or wake))
            def steal(at):
                self.assertEqual(at, wake)
                events.append("steal")
                if error:
                    raise error
            theft = stack.enter_context(patch.object(app, "visit_pinned_friends", side_effect=steal))
            stack.enter_context(patch.object(app, "_exit_game_for_wait", side_effect=lambda: events.append("exit")))
            def wait(at):
                self.assertEqual(at, wake)
                events.append("wait")
                raise Done
            stack.enter_context(patch.object(app, "wait_for_next_round", side_effect=wait))
            with self.assertRaises(Done):
                app.main()
        return events, calculate.call_args.kwargs, theft.call_count

    def test_main_preserves_wake_time_and_exit_even_after_failure(self):
        for app in (w,):
            for error in (None, RuntimeError("ADB disconnected")):
                with self.subTest(app=app.__name__, error=error):
                    events, kwargs, count = self.run_round(app, error=error)
                    self.assertEqual(events, ["schedule", "steal", "exit", "wait"])
                    self.assertFalse(kwargs["exit_game"])
                    self.assertEqual(count, 1)

    def test_disabled_unknown_or_mature_round_skips_friends(self):
        for options in ({"enabled": "0"}, {"plan": False}, {"mature": True}, {"mature": None}):
            with self.subTest(options=options):
                events, kwargs, count = self.run_round(w, **options)
                self.assertEqual(events, ["schedule", "wait"])
                self.assertNotIn("exit_game", kwargs)
                self.assertEqual(count, 0)
