"""Restart loads new code without farming before the saved wake/target times."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from farmwatch import __main__ as cli
from farmwatch.runtime import process
from tests.support import make_app

WAKE = datetime(2026, 11, 14, 23, 24, 24, 687135)
TARGET = WAKE + timedelta(minutes=5)


class ResumePlanTests(unittest.TestCase):
    def test_saved_absolute_target_survives_changed_wake_ahead_setting(self):
        record = {"phase": "waiting", "wake_time": WAKE.isoformat(), "target_time": TARGET.isoformat()}
        self.assertEqual(process.waiting_resume_plan(record, wake_ahead_min=1), (WAKE, TARGET))

    def test_legacy_worker_target_is_recovered_from_the_confirmed_wake_ahead(self):
        self.assertEqual(process.waiting_resume_plan({"phase": "waiting", "wake_time": WAKE.isoformat()}), (WAKE, TARGET))

    def test_explicit_unknown_target_does_not_add_an_extra_wake_ahead(self):
        record = {"phase": "waiting", "wake_time": WAKE.isoformat(), "target_time": None}
        self.assertEqual(process.waiting_resume_plan(record), (WAKE, None))

    def test_nonwaiting_worker_does_not_resume_an_old_schedule(self):
        self.assertEqual(process.waiting_resume_plan({"phase": "running", "wake_time": WAKE.isoformat()}), (None, None))
        self.assertEqual(process.waiting_resume_plan({}), (None, None))

    def test_invalid_waiting_plan_is_rejected_before_the_worker_is_stopped(self):
        for record in ({"phase": "waiting"}, {"phase": "waiting", "wake_time": "broken"},
                       {"phase": "waiting", "wake_time": WAKE.isoformat(), "target_time": (WAKE-timedelta(seconds=1)).isoformat()}):
            with self.subTest(record=record), self.assertRaisesRegex(RuntimeError, "未停止"):
                process.waiting_resume_plan(record)

    def test_aware_timestamps_cannot_be_mixed_with_the_local_clock(self):
        with self.assertRaises(RuntimeError):
            process.waiting_resume_plan({"phase": "waiting", "wake_time": WAKE.isoformat()+"+08:00"})

    def test_legacy_plan_rolls_target_into_the_next_day_correctly(self):
        wake = datetime(2026, 11, 14, 23, 58)
        self.assertEqual(process.waiting_resume_plan({"phase": "waiting", "wake_time": wake.isoformat()}),
                         (wake, datetime(2026, 11, 15, 0, 3)))


class RestartCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.read = self.stack.enter_context(patch.object(process, "read_state", return_value={
            "phase": "waiting", "wake_time": WAKE.isoformat(), "target_time": TARGET.isoformat()
        }))
        self.stop = self.stack.enter_context(patch.object(process, "stop", return_value=0))
        self.start = self.stack.enter_context(patch.object(process, "start_background", return_value=0))

    def test_stop_precedes_restart_and_both_absolute_times_are_forwarded(self):
        events = Mock()
        events.attach_mock(self.stop, "stop")
        events.attach_mock(self.start, "start")
        self.assertEqual(process.restart_background(self.root), 0)
        self.assertEqual(events.mock_calls, [
            call.stop(self.root), call.start(self.root, config_path=None, resume_wake_time=WAKE, resume_target_time=TARGET)
        ])

    def test_stop_failure_never_creates_a_second_worker(self):
        self.stop.return_value = 1
        self.assertEqual(process.restart_background(self.root), 1)
        self.start.assert_not_called()

    def test_invalid_wait_plan_leaves_the_current_worker_intact(self):
        self.read.return_value = {"phase": "waiting", "wake_time": "invalid"}
        with self.assertRaises(RuntimeError):
            process.restart_background(self.root)
        self.stop.assert_not_called()
        self.start.assert_not_called()

    def test_nonwaiting_restart_starts_normally_without_stale_wait_flags(self):
        self.read.return_value = {"phase": "running", "wake_time": WAKE.isoformat()}
        self.assertEqual(process.restart_background(self.root), 0)
        self.start.assert_called_once_with(self.root, config_path=None, resume_wake_time=None, resume_target_time=None)


class ResumeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app._resume_wake_time = WAKE
        self.app._pending_target_time = TARGET
        self.app._pending_target_is_absolute = True

    def test_resumed_wait_happens_before_emulator_launch_or_any_farming(self):
        class StopAtWait(Exception): pass
        with patch.object(self.app, "wait_for_next_round", side_effect=StopAtWait) as wait, \
                patch.object(self.app, "ensure_emulator_running") as ensure, \
                patch.object(self.app, "perform_farm_action") as click, redirect_stdout(StringIO()):
            with self.assertRaises(StopAtWait):
                self.app.main()
        wait.assert_called_once_with(WAKE)
        ensure.assert_not_called()
        click.assert_not_called()
        self.assertEqual(self.app._pending_target_time, TARGET)
        self.assertIsNone(self.app._resume_wake_time)

    def test_failed_resumed_wait_does_not_boot_the_game(self):
        with patch.object(self.app, "wait_for_next_round", return_value=False), \
                patch.object(self.app, "ensure_emulator_running") as ensure, redirect_stdout(StringIO()):
            self.assertEqual(self.app.main(), 1)
        ensure.assert_not_called()

    def test_absolute_restart_target_is_not_clamped_by_new_wake_ahead_setting(self):
        now = TARGET - timedelta(minutes=4)
        self.app.datetime = SimpleNamespace(now=lambda: now)
        self.app.WAKE_AHEAD_MIN = 0
        with patch.object(self.app.time, "sleep") as sleep, redirect_stdout(StringIO()):
            self.assertTrue(self.app.wait_until_target_time())
        sleep.assert_called_once_with(240.0)
        self.assertIsNone(self.app._pending_target_time)
        self.assertFalse(self.app._pending_target_is_absolute)

    def test_absolute_wait_override_is_consumed_and_does_not_affect_later_plans(self):
        now = TARGET - timedelta(minutes=4)
        self.app.datetime = SimpleNamespace(now=lambda: now)
        self.app.WAKE_AHEAD_MIN = 1
        with patch.object(self.app.time, "sleep") as sleep, redirect_stdout(StringIO()):
            self.app.wait_until_target_time()
            self.app._pending_target_time = now + timedelta(hours=1)
            self.app.wait_until_target_time()
        self.assertEqual(sleep.call_args_list, [call(240.0), call(60)])

    def test_navigation_failures_keep_the_unconsumed_absolute_target(self):
        self.app._resume_wake_time = None
        with ExitStack() as stack, redirect_stdout(StringIO()):
            for name, value in {
                "ensure_emulator_running": True, "check_adb_connection": True,
                "detect_resolution": (1280, 720), "launch_game": True,
                "wait_for_game_update": True, "ensure_account_ready": True,
                "enter_game": True, "open_farm": True, "approach_farm_control": False,
            }.items():
                stack.enter_context(patch.object(self.app, name, return_value=value))
            for name in ("report_device_state", "dismiss_startup_overlays", "dismiss_lobby_overlays", "_exit_game_for_wait"):
                stack.enter_context(patch.object(self.app, name))
            click = stack.enter_context(patch.object(self.app, "perform_farm_action"))
            stack.enter_context(patch.object(self.app.time, "sleep"))
            with self.assertRaisesRegex(RuntimeError, "连续三轮失败"):
                self.app.main()
        click.assert_not_called()
        self.assertEqual(self.app._pending_target_time, TARGET)
        self.assertTrue(self.app._pending_target_is_absolute)

    def test_waiting_status_persists_both_wake_and_target(self):
        self.app.datetime = SimpleNamespace(now=lambda: WAKE-timedelta(hours=1))
        self.app.status_callback = Mock()
        with patch.object(self.app, "shutdown_emulator_for_wait"), \
                patch.object(self.app.time, "sleep"), \
                patch.object(self.app, "ensure_emulator_running", return_value=True), redirect_stdout(StringIO()):
            self.assertTrue(self.app.wait_for_next_round(WAKE))
        self.app.status_callback.assert_any_call("waiting", wake_time=WAKE.isoformat(), target_time=TARGET.isoformat())

    def test_unknown_retry_target_is_persisted_explicitly_as_null(self):
        self.app._pending_target_time = None
        self.app.datetime = SimpleNamespace(now=lambda: WAKE-timedelta(hours=1))
        self.app.status_callback = Mock()
        with patch.object(self.app, "shutdown_emulator_for_wait"), \
                patch.object(self.app.time, "sleep"), \
                patch.object(self.app, "ensure_emulator_running", return_value=True), redirect_stdout(StringIO()):
            self.app.wait_for_next_round(WAKE)
        self.app.status_callback.assert_any_call("waiting", wake_time=WAKE.isoformat(), target_time=None)


class RestartCliTests(unittest.TestCase):
    def test_cli_dispatches_restart_with_current_configured_wake_ahead(self):
        settings = make_app().settings
        with patch.object(cli, "validate_runtime"), patch.object(cli, "load_settings", return_value=settings), \
                patch.object(process, "restart_background", return_value=0) as restart:
            self.assertEqual(cli.main(["restart"]), 0)
        restart.assert_called_once_with(cli.ROOT, config_path=None, wake_ahead_min=settings.wake_ahead_min)

    def test_resume_target_without_wake_is_rejected_before_any_process_action(self):
        with patch.object(cli, "validate_runtime") as validate, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["run", "--resume-target-time", TARGET.isoformat()])
        validate.assert_not_called()

    def test_resume_flags_are_not_allowed_on_unrelated_actions(self):
        with patch.object(cli, "validate_runtime") as validate, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["stop", "--resume-wake-time", WAKE.isoformat()])
        validate.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows detached worker launch")
class ResumeWorkerArgumentsTests(unittest.TestCase):
    def test_background_child_receives_resume_times_as_separate_safe_arguments(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory).resolve()
            run_id = "a"*32
            record = {"run_id": run_id, "phase": "waiting", "pid": 123}
            with patch.object(process, "is_running", side_effect=[False, True]), \
                    patch.object(process, "read_state", return_value=record), \
                    patch.object(process.uuid, "uuid4", return_value=SimpleNamespace(hex=run_id)), \
                    patch.object(process.subprocess, "Popen") as popen:
                self.assertEqual(process.start_background(root, resume_wake_time=WAKE, resume_target_time=TARGET), 0)
            command = popen.call_args.args[0]
            self.assertEqual(command[-4:], ["--resume-wake-time", WAKE.isoformat(), "--resume-target-time", TARGET.isoformat()])
            self.assertEqual(popen.call_args.kwargs["cwd"], root)
