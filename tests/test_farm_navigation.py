"""Bounded statue navigation and explicit, fresh-frame origin resets."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import subprocess
import unittest
from unittest.mock import Mock, call, patch

from farmwatch.domain.results import RoundStatus
from tests.support import make_app


BUTTON = {"x": 855, "y": 413, "score": .91}
PATH = {"angle": 120, "distance": 200, "duration": 1500, "center": (160, 486)}
FULL = call(120, 200, 1500, center=(160, 486))
NUDGE = call(120, 200, 200, center=(160, 486))


class FarmNavigationTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.stack.enter_context(patch.object(self.app, "statue_path", PATH.copy()))
        self.reset = self.stack.enter_context(patch.object(self.app, "reset_position", return_value=True))
        self.move = self.stack.enter_context(patch.object(self.app, "move_joystick"))
        self.capture = self.stack.enter_context(patch.object(self.app, "screenshot"))
        self.detect = self.stack.enter_context(patch.object(self.app, "locate_farm_action", return_value=None))
        self.stack.enter_context(patch.object(self.app.time, "sleep"))

    def test_already_at_button_does_not_reset_or_move(self):
        self.detect.return_value = BUTTON
        self.assertTrue(self.app.approach_farm_control())
        self.reset.assert_not_called()
        self.move.assert_not_called()
        self.capture.assert_called_once_with(self.app.SCREENSHOT_PATH)

    def test_full_route_starts_with_reset_and_keeps_configured_duration(self):
        self.detect.side_effect = [None, BUTTON]
        actions = Mock()
        actions.attach_mock(self.reset, "reset")
        actions.attach_mock(self.move, "move")
        self.assertTrue(self.app.approach_farm_control())
        self.assertEqual(actions.mock_calls, [call.reset(), call.move(*FULL.args, **FULL.kwargs)])

    def test_short_move_is_supplemented_by_one_small_step(self):
        self.detect.side_effect = [None, None, BUTTON]
        self.assertTrue(self.app.approach_farm_control())
        self.reset.assert_called_once_with()
        self.assertEqual(self.move.call_args_list, [FULL, NUDGE])

    def test_each_nudge_is_checked_and_never_grows(self):
        self.detect.side_effect = [None, None, None, None, BUTTON]
        self.assertTrue(self.app.approach_farm_control())
        self.assertEqual(self.move.call_args_list, [FULL, NUDGE, NUDGE, NUDGE])
        self.assertEqual(self.detect.call_count, 5)
        self.reset.assert_called_once_with()

    def test_failed_nudges_reset_before_repeating_the_same_full_route(self):
        self.detect.side_effect = [None] * 5 + [BUTTON]
        actions = Mock()
        actions.attach_mock(self.reset, "reset")
        actions.attach_mock(self.move, "move")
        self.assertTrue(self.app.approach_farm_control())
        expected = [call.reset(), call.move(*FULL.args, **FULL.kwargs)]
        expected += [call.move(*NUDGE.args, **NUDGE.kwargs)] * 3
        expected += [call.reset(), call.move(*FULL.args, **FULL.kwargs)]
        self.assertEqual(actions.mock_calls, expected)

    def test_complete_failure_has_only_three_routes_and_nine_small_steps(self):
        self.assertFalse(self.app.approach_farm_control())
        self.assertEqual(self.reset.call_count, 3)
        self.assertEqual(self.move.call_args_list, [FULL, NUDGE, NUDGE, NUDGE] * 3)
        self.capture.assert_called_with(self.app.SCREENSHOT_PATH, archive_label="approach_fail")

    def test_caller_can_limit_reset_retries(self):
        self.assertFalse(self.app.approach_farm_control(max_attempts=1))
        self.reset.assert_called_once_with()
        self.assertEqual(self.move.call_args_list, [FULL, NUDGE, NUDGE, NUDGE])

    def test_initial_reset_failure_does_not_move_from_unknown_position(self):
        self.reset.return_value = False
        self.assertFalse(self.app.approach_farm_control())
        self.reset.assert_called_once_with()
        self.move.assert_not_called()

    def test_later_reset_failure_does_not_repeat_full_move(self):
        self.reset.side_effect = [True, False]
        self.assertFalse(self.app.approach_farm_control())
        self.assertEqual(self.reset.call_count, 2)
        self.assertEqual(self.move.call_args_list, [FULL, NUDGE, NUDGE, NUDGE])


class PositionResetTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.capture = self.stack.enter_context(patch.object(self.app, "screenshot"))
        self.detect = self.stack.enter_context(patch.object(self.app, "locate_control", return_value={"x": 1186, "y": 653}))
        self.adb = self.stack.enter_context(patch.object(self.app, "adb_shell_result", return_value=subprocess.CompletedProcess([], 0)))
        self.sleep = self.stack.enter_context(patch.object(self.app.time, "sleep"))

    def test_reset_captures_a_fresh_frame_before_matching_and_clicking(self):
        actions = Mock()
        for name, child in (("capture", self.capture), ("detect", self.detect), ("adb", self.adb), ("sleep", self.sleep)):
            actions.attach_mock(child, name)
        self.assertTrue(self.app.reset_position())
        self.assertEqual(actions.mock_calls, [
            call.capture(self.app.SCREENSHOT_PATH),
            call.detect("farm.reset", self.app.SCREENSHOT_PATH, .5),
            call.adb("input tap 1186 653"),
            call.sleep(5),
        ])

    def test_missing_reset_control_never_clicks(self):
        self.detect.return_value = None
        self.assertFalse(self.app.reset_position())
        self.adb.assert_not_called()
        self.sleep.assert_not_called()

    def test_rejected_reset_is_not_reported_as_success(self):
        self.adb.return_value = subprocess.CompletedProcess([], 1)
        self.assertFalse(self.app.reset_position())
        self.sleep.assert_not_called()

    def test_adb_timeout_or_os_error_is_a_reset_failure(self):
        for error in (subprocess.TimeoutExpired(["adb"], 15), OSError("device closed")):
            with self.subTest(error=type(error).__name__):
                self.adb.side_effect = error
                self.assertFalse(self.app.reset_position())
                self.sleep.assert_not_called()


class NavigationMainGateTests(unittest.TestCase):
    def check_failed_rounds(self, positions):
        app = make_app()
        with ExitStack() as stack, redirect_stdout(StringIO()):
            for name, value in {
                "ensure_emulator_running": True, "check_adb_connection": True,
                "detect_resolution": (1280, 720), "launch_game": True,
                "wait_for_game_update": True, "ensure_account_ready": True,
                "enter_game": True, "open_farm": True,
            }.items():
                stack.enter_context(patch.object(app, name, return_value=value))
            for name in ("report_device_state", "dismiss_startup_overlays", "dismiss_lobby_overlays", "screenshot", "_exit_game_for_wait"):
                stack.enter_context(patch.object(app, name))
            approach = stack.enter_context(patch.object(app, "approach_farm_control", side_effect=positions))
            click = stack.enter_context(patch.object(app, "perform_farm_action", return_value=False))
            wait = stack.enter_context(patch.object(app, "wait_until_target_time"))
            readers = [stack.enter_context(patch.object(app, name)) for name in (
                "collect_harvest_result", "inspect_crop", "schedule_next_round", "notify_round"
            )]
            stack.enter_context(patch.object(app.time, "sleep"))
            with self.assertRaisesRegex(RuntimeError, "连续三轮失败"):
                app.main()
            for reader in readers:
                reader.assert_not_called()
            self.assertEqual(app.last_round.status, RoundStatus.ACTION_FAILED)
        return approach, click, wait

    def test_failed_navigation_never_clicks_or_repeats_the_unconfirmed_route(self):
        approach, click, wait = self.check_failed_rounds([False] * 3)
        self.assertEqual(approach.call_count, 3)
        click.assert_not_called()
        wait.assert_not_called()

    def test_failed_reposition_does_not_send_another_click(self):
        approach, click, wait = self.check_failed_rounds([True, False] * 3)
        self.assertEqual(approach.call_count, 6)
        self.assertEqual(click.call_count, 3)
        self.assertEqual(wait.call_count, 3)
