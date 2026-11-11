import tempfile
import shlex
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import call, patch

import cv2
import numpy as np

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
ADB_PATH = r"C:\Program Files (x86)\platform-tools\adb.exe"
DEVICE = "127.0.0.1:16704"


class AdbCommandTests(unittest.TestCase):
    def test_wake_screen_continues_when_power_query_times_out(self):
        timeout = subprocess.TimeoutExpired(
            [ADB_PATH, "-s", DEVICE, "shell", "dumpsys power"], 10
        )
        success = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        output = StringIO()

        with (
            patch.object(app, "ADB", ADB_PATH),
            patch.object(app, "DEVICE", DEVICE),
            patch.object(
                app.subprocess, "run", side_effect=[timeout, success, success]
            ) as run,
            patch.object(app.time, "sleep"),
            redirect_stdout(output),
        ):
            result = app.wake_screen()

        self.assertTrue(result)
        self.assertEqual(run.call_count, 3)
        self.assertIn("ADB shell 超时", output.getvalue())

    def test_launch_waits_40_seconds_after_game_process_is_detected(self):
        state_ready = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="device\n", stderr=""
        )
        launch_ok = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Status: ok\n", stderr=""
        )

        with (
            patch.object(app.subprocess, "run", return_value=state_ready),
            patch.object(app, "adb_shell_result", return_value=launch_ok),
            patch.object(
                app,
                "adb_shell",
                side_effect=lambda command: "1234\n" if command.startswith("pidof") else "",
            ),
            patch.object(app.time, "sleep") as sleep,
        ):
            result = app.launch_game()

        self.assertTrue(result)
        sleep.assert_called_once_with(40)

    def test_launch_checks_state_with_host_adb_get_state(self):
        state_failure = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: device offline\n"
        )

        with (
            patch.object(app, "ADB", ADB_PATH),
            patch.object(app, "DEVICE", DEVICE),
            patch.object(
                app.subprocess, "run", return_value=state_failure
            ) as run,
        ):
            result = app.launch_game()

        self.assertFalse(result)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], [ADB_PATH, "-s", DEVICE, "get-state"])
        self.assertFalse(kwargs.get("shell", False))

    def test_adb_shell_uses_argument_list_without_shell(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="device\n", stderr=""
        )

        with (
            patch.object(app, "ADB", ADB_PATH),
            patch.object(app, "DEVICE", DEVICE),
            patch.object(app.subprocess, "run", return_value=completed) as run,
        ):
            result = app.adb_shell("get-state")

        self.assertEqual(result, "device\n")
        run.assert_called_once()
        args, kwargs = run.call_args
        with self.subTest("command is an argument list"):
            self.assertEqual(
                args[0],
                [ADB_PATH, "-s", DEVICE, "shell", "get-state"],
            )
        with self.subTest("shell is not enabled"):
            self.assertFalse(kwargs.get("shell", False))


    def test_screenshot_pull_uses_argument_list_for_path_with_spaces(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            capture_dir = Path(tmpdir) / "Screen Captures"
            path = capture_dir / "screen image.png"

            def run_adb(args, **_kwargs):
                if "pull" in args:
                    pulled_path = Path(args[-1])
                    pulled_path.parent.mkdir(parents=True, exist_ok=True)
                    portrait = np.full((30, 20, 3), 127, dtype="uint8")
                    self.assertTrue(cv2.imwrite(str(pulled_path), portrait))
                return completed

            with (
                patch.object(app, "ADB", ADB_PATH),
                patch.object(app, "DEVICE", DEVICE),
                patch.object(
                    app,
                    "SCREENSHOT_ARCHIVE_DIR",
                    Path(tmpdir) / "farm_screenshots",
                    create=True,
                ),
                patch.object(
                    app.subprocess, "run", side_effect=run_adb
                ) as run,
            ):
                returned = app.screenshot(path)

            self.assertEqual(returned, path)
            self.assertEqual(run.call_count, 2)
            pull_call = next(
                adb_call
                for adb_call in run.call_args_list
                if "pull" in adb_call.args[0]
            )
            pull_args = pull_call.args[0]
            self.assertEqual(
                pull_args[:5],
                [ADB_PATH, "-s", DEVICE, "pull", "/sdcard/screen.png"],
            )
            self.assertNotEqual(Path(pull_args[-1]), path)
            self.assertEqual(Path(pull_args[-1]).parent, path.parent)
            self.assertFalse(pull_call.kwargs.get("shell", False))
            final_image = cv2.imread(str(path))
            self.assertEqual(final_image.shape[:2], (20, 30))
            self.assertEqual(list(path.parent.glob("*.png")), [path])

    def test_screenshot_retries_transient_pull_failure_then_succeeds(self):
        success = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        pull_failure = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="temporary pull failure"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "current.png"
            old_image = np.full((20, 30, 3), 50, dtype="uint8")
            new_image = np.full((20, 30, 3), 210, dtype="uint8")
            self.assertTrue(cv2.imwrite(str(path), old_image))
            pull_attempts = {"count": 0}

            def run_adb(args, **_kwargs):
                if "pull" not in args:
                    return success
                pull_attempts["count"] += 1
                if pull_attempts["count"] == 1:
                    return pull_failure
                self.assertTrue(cv2.imwrite(str(Path(args[-1])), new_image))
                return success

            with (
                patch.object(
                    app,
                    "SCREENSHOT_ARCHIVE_DIR",
                    Path(tmpdir) / "farm_screenshots",
                    create=True,
                ),
                patch.object(app.subprocess, "run", side_effect=run_adb) as run,
                patch.object(app.time, "sleep") as sleep,
            ):
                returned = app.screenshot(path)

            self.assertEqual(returned, path)
            self.assertEqual(run.call_count, 4)
            sleep.assert_called_once_with(app.SCREENSHOT_RETRY_INTERVAL)
            final_image = cv2.imread(str(path))
            self.assertTrue(np.all(final_image == 210))
            self.assertEqual(list(Path(tmpdir).glob("*.png")), [path])

    def test_screenshot_failure_preserves_concurrent_shared_target_and_raises(self):
        screencap_success = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        pull_failure = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="pull failed"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "current.png"
            initial_image = np.full((20, 30, 3), 50, dtype="uint8")
            concurrent_image = np.full((20, 30, 3), 220, dtype="uint8")
            self.assertTrue(cv2.imwrite(str(path), initial_image))

            def run_adb(args, **_kwargs):
                if "pull" in args:
                    # Model another screenshot call successfully replacing the
                    # shared target while this call's private pull fails.
                    self.assertTrue(cv2.imwrite(str(path), concurrent_image))
                    return pull_failure
                return screencap_success

            with (
                patch.object(
                    app,
                    "SCREENSHOT_ARCHIVE_DIR",
                    Path(tmpdir) / "farm_screenshots",
                    create=True,
                ),
                patch.object(app.subprocess, "run", side_effect=run_adb),
                patch.object(app.time, "sleep") as sleep,
            ):
                with self.assertRaises(app.ScreenshotCaptureError):
                    app.screenshot(path)

            self.assertTrue(path.exists())
            final_image = cv2.imread(str(path))
            self.assertTrue(np.all(final_image == 220))
            self.assertEqual(list(Path(tmpdir).glob("*.png")), [path])
            self.assertEqual(
                sleep.call_count, app.SCREENSHOT_MAX_ATTEMPTS - 1
            )

    def test_check_adb_connection_uses_host_get_state_and_skips_connect_when_connected(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="device\n", stderr=""
        )
        output = StringIO()

        with (
            patch.object(app, "ADB", ADB_PATH),
            patch.object(app, "DEVICE", DEVICE),
            patch.object(app.subprocess, "run", return_value=completed) as run,
            redirect_stdout(output),
        ):
            result = app.check_adb_connection()

        self.assertTrue(result)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], [ADB_PATH, "-s", DEVICE, "get-state"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn("设备已连接", output.getvalue())

    def test_check_adb_connection_reports_connect_stderr_and_uses_argument_list(self):
        get_state_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="error: device offline\n",
        )
        connect_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="cannot connect to 127.0.0.1:16704\n",
        )
        output = StringIO()

        with (
            patch.object(app, "ADB", ADB_PATH),
            patch.object(app, "DEVICE", DEVICE),
            patch.object(
                app.subprocess,
                "run",
                side_effect=[get_state_result, connect_result],
            ) as run,
            redirect_stdout(output),
        ):
            result = app.check_adb_connection()

        self.assertFalse(result)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list,
            [
                call(
                    [ADB_PATH, "-s", DEVICE, "get-state"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    creationflags=app._SUBPROCESS_FLAGS,
                ),
                call(
                    [ADB_PATH, "connect", DEVICE],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    creationflags=app._SUBPROCESS_FLAGS,
                ),
            ],
        )
        with self.subTest("stderr is printed"):
            self.assertIn("cannot connect to 127.0.0.1:16704", output.getvalue())
        with self.subTest("shell is not enabled"):
            for adb_call in run.call_args_list:
                self.assertFalse(adb_call.kwargs.get("shell", False))

    def test_adb_state_parser_does_not_accept_offline_diagnostic(self):
        self.assertTrue(app._adb_state_is_ready("device\n"))
        self.assertFalse(app._adb_state_is_ready("error: device offline\n"))
        self.assertFalse(app._adb_state_is_ready("no devices/emulators found\n"))


if __name__ == "__main__":
    unittest.main()
