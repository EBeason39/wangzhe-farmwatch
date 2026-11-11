import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class ScreenshotArchiveTests(unittest.TestCase):
    CAPTURE_ARCHIVE_NAME_PATTERN = (
        r"^device_capture_"
        r"\d{8}_\d{6}_\d{6}_[0-9a-f]{8}\.png$"
    )

    def _capture(
        self,
        root,
        image,
        *,
        archive_label="capture",
        run_side_effect=None,
    ):
        root = Path(root)
        target = root / "current.png"
        archive_dir = root / "farm_screenshots"
        success = subprocess.CompletedProcess([], 0, "", "")

        def default_run(args, **_kwargs):
            if "pull" in args:
                self.assertTrue(cv2.imwrite(str(Path(args[-1])), image))
            return success

        with (
            patch.object(
                app,
                "SCREENSHOT_ARCHIVE_DIR",
                archive_dir,
                create=True,
            ),
            patch.object(
                app.subprocess,
                "run",
                side_effect=run_side_effect or default_run,
            ) as run,
        ):
            returned = app.screenshot(
                target,
                archive_label=archive_label,
            )

        return target, archive_dir, returned, run

    def test_success_keeps_current_and_archives_same_frame_without_extra_adb(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image = np.full((20, 30, 3), 137, dtype="uint8")

            target, archive_dir, returned, run = self._capture(tmpdir, image)

            self.assertEqual(returned, target)
            self.assertEqual(run.call_count, 2)
            archives = list(archive_dir.iterdir())
            self.assertEqual(len(archives), 1)
            self.assertRegex(
                archives[0].name,
                self.CAPTURE_ARCHIVE_NAME_PATTERN,
            )
            np.testing.assert_array_equal(cv2.imread(str(target)), image)
            np.testing.assert_array_equal(cv2.imread(str(archives[0])), image)

    def test_archive_contains_rotated_final_frame_not_raw_portrait(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            portrait = np.zeros((30, 20, 3), dtype="uint8")
            portrait[:, :10] = (10, 20, 30)
            portrait[:, 10:] = (110, 120, 130)
            portrait[:5, :] = (210, 220, 230)
            expected = cv2.rotate(portrait, cv2.ROTATE_90_COUNTERCLOCKWISE)

            target, archive_dir, _, _ = self._capture(tmpdir, portrait)

            archives = list(archive_dir.glob("device_capture_*.png"))
            self.assertEqual(len(archives), 1)
            current = cv2.imread(str(target))
            archived = cv2.imread(str(archives[0]))
            self.assertEqual(current.shape[:2], (20, 30))
            np.testing.assert_array_equal(current, expected)
            np.testing.assert_array_equal(archived, expected)

    def test_same_timestamp_captures_have_unique_names_and_keep_both_frames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixed_now = app.time.time()
            first = np.full((20, 30, 3), 10, dtype="uint8")
            second = np.full((20, 30, 3), 20, dtype="uint8")

            with patch.object(app.time, "time", return_value=fixed_now):
                self._capture(tmpdir, first)
                self._capture(tmpdir, second)

            archive_dir = Path(tmpdir) / "farm_screenshots"
            archives = sorted(archive_dir.glob("device_capture_*.png"))
            self.assertEqual(len(archives), 2)
            self.assertNotEqual(archives[0].name, archives[1].name)
            for archive in archives:
                self.assertRegex(
                    archive.name,
                    self.CAPTURE_ARCHIVE_NAME_PATTERN,
                )

            archived_values = sorted(
                int(cv2.imread(str(archive))[0, 0, 0]) for archive in archives
            )
            self.assertEqual(archived_values, [10, 20])

    def test_cleanup_deletes_only_matching_regular_files_strictly_older_than_72_hours(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_dir = root / "farm_screenshots"
            archive_dir.mkdir()
            now = 2_000_000_000.0
            cutoff = now - 72 * 60 * 60

            files = {
                "old_capture": archive_dir
                / "device_capture_20200101_000000_000000_deadbeef.png",
                "old_farm_entry": archive_dir
                / "device_farm_entry_20200101_000001_000000_cafebabe.png",
                "edge": archive_dir
                / "device_capture_20200101_000002_000000_0123abcd.png",
                "new": archive_dir
                / "device_farm_entry_20200101_000003_000000_abcdef01.png",
                "future": archive_dir
                / "device_capture_20200101_000004_000000_12345678.png",
                "manual": archive_dir / "manual.png",
                "notes": archive_dir / "device_capture_notes.png",
                "non_png": archive_dir
                / "device_capture_20200101_000005_000000_deadbeef.jpg",
                "bad_uuid": archive_dir
                / "device_capture_20200101_000006_000000_notuuid8.png",
            }
            for path in files.values():
                path.write_bytes(b"x")

            matching_directory = (
                archive_dir
                / "device_capture_20200101_000007_000000_87654321.png"
            )
            matching_directory.mkdir()

            nested_dir = archive_dir / "nested"
            nested_dir.mkdir()
            nested = (
                nested_dir
                / "device_capture_20200101_000008_000000_a1b2c3d4.png"
            )
            nested.write_bytes(b"x")

            outside = (
                root / "device_capture_20200101_000009_000000_0badcafe.png"
            )
            outside.write_bytes(b"x")

            old_mtime = cutoff - 1
            os.utime(files["old_capture"], (old_mtime, old_mtime))
            os.utime(files["old_farm_entry"], (old_mtime, old_mtime))
            os.utime(files["edge"], (cutoff, cutoff))
            os.utime(files["new"], (cutoff + 1, cutoff + 1))
            os.utime(files["future"], (now + 1, now + 1))
            for key in ("manual", "notes", "non_png", "bad_uuid"):
                os.utime(files[key], (old_mtime, old_mtime))
            os.utime(matching_directory, (old_mtime, old_mtime))
            os.utime(nested, (old_mtime, old_mtime))
            os.utime(outside, (old_mtime, old_mtime))

            with patch.object(
                app,
                "SCREENSHOT_ARCHIVE_DIR",
                archive_dir,
                create=True,
            ):
                app._cleanup_screenshot_archive(now=now)

            self.assertFalse(files["old_capture"].exists())
            self.assertFalse(files["old_farm_entry"].exists())
            for key in (
                "edge",
                "new",
                "future",
                "manual",
                "notes",
                "non_png",
                "bad_uuid",
            ):
                self.assertTrue(files[key].exists(), key)
            self.assertTrue(matching_directory.is_dir())
            self.assertTrue(nested.exists())
            self.assertTrue(outside.exists())

    def test_archive_preparation_failure_warns_but_returns_successful_current_frame(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image = np.full((20, 30, 3), 77, dtype="uint8")
            output = StringIO()

            with (
                patch.object(
                    app,
                    "_stage_screenshot_archive",
                    side_effect=OSError("archive locked"),
                ),
                redirect_stdout(output),
            ):
                target, _, returned, run = self._capture(tmpdir, image)

            self.assertEqual(returned, target)
            self.assertEqual(run.call_count, 2)
            np.testing.assert_array_equal(cv2.imread(str(target)), image)
            self.assertIn("截图归档失败", output.getvalue())

    def test_archive_publish_failure_warns_and_leaves_no_archive_temp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image = np.full((20, 30, 3), 88, dtype="uint8")
            output = StringIO()
            real_replace = os.replace
            replace_calls = {"count": 0}

            def fail_archive_publish(source, destination):
                replace_calls["count"] += 1
                if replace_calls["count"] == 1:
                    return real_replace(source, destination)
                raise OSError("archive publish locked")

            with (
                patch.object(
                    app.os,
                    "replace",
                    side_effect=fail_archive_publish,
                ),
                redirect_stdout(output),
            ):
                target, archive_dir, returned, run = self._capture(tmpdir, image)

            self.assertEqual(returned, target)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(replace_calls["count"], 2)
            np.testing.assert_array_equal(cv2.imread(str(target)), image)
            self.assertIn("截图归档发布失败", output.getvalue())
            self.assertEqual(list(archive_dir.glob("device_*.png")), [])
            self.assertEqual(
                [path for path in archive_dir.iterdir() if path.name.endswith(".tmp")],
                [],
            )

    def test_cleanup_failure_warns_after_publishing_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image = np.full((20, 30, 3), 99, dtype="uint8")
            output = StringIO()

            with (
                patch.object(
                    app,
                    "_cleanup_screenshot_archive",
                    side_effect=OSError("cleanup denied"),
                ) as cleanup,
                redirect_stdout(output),
            ):
                target, archive_dir, returned, run = self._capture(tmpdir, image)

            self.assertEqual(returned, target)
            self.assertEqual(run.call_count, 2)
            cleanup.assert_called_once_with()
            np.testing.assert_array_equal(cv2.imread(str(target)), image)
            archives = list(archive_dir.glob("device_capture_*.png"))
            self.assertEqual(len(archives), 1)
            np.testing.assert_array_equal(cv2.imread(str(archives[0])), image)
            self.assertIn("截图归档清理失败", output.getvalue())

    def test_device_offline_triggers_recovery_before_retry(self):
        offline = subprocess.CompletedProcess(
            [], 1, "", "adb.EXE: device offline"
        )
        success = subprocess.CompletedProcess([], 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            image = np.full((20, 30, 3), 177, dtype="uint8")
            calls = {"count": 0}

            def run_adb(args, **_kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    return offline
                if "pull" in args:
                    self.assertTrue(cv2.imwrite(str(Path(args[-1])), image))
                return success

            with (
                patch.object(
                    app, "recover_adb_connection", return_value=True
                ) as recover,
                patch.object(app.time, "sleep"),
            ):
                target, _, returned, run = self._capture(
                    tmpdir, image, run_side_effect=run_adb
                )

            self.assertEqual(returned, target)
            recover.assert_called_once_with()
            self.assertEqual(run.call_count, 3)
            np.testing.assert_array_equal(cv2.imread(str(target)), image)

    def test_recovery_restarts_managed_mumu_and_updates_dynamic_device(self):
        def restore_emulator(timeout, stale_restart_delay):
            app.DEVICE = "127.0.0.1:5555"
            get_state.return_value = "device"
            return True
        with (
            patch.object(app, "_manage_mumu", True),
            patch.object(
                app, "ADB_RECOVERY_EMULATOR_TIMEOUT_SEC", 300
            ),
            patch.object(
                app, "ADB_RECOVERY_STALE_RESTART_DELAY_SEC", 150
            ),
            patch.object(app, "DEVICE", "127.0.0.1:16416"),
            patch.object(
                app,
                "adb_get_state",
                return_value="error: device not found",
            ) as get_state,
            patch.object(app.subprocess, "run") as run_adb,
            patch.object(app.time, "sleep"),
            patch.object(
                app,
                "ensure_emulator_running",
                side_effect=restore_emulator,
            ) as ensure,
        ):
            self.assertTrue(app.recover_adb_connection())

            self.assertEqual(app.DEVICE, "127.0.0.1:5555")
        ensure.assert_called_once_with(
            300,
            app.ADB_RECOVERY_STALE_RESTART_DELAY_SEC,
        )
        self.assertEqual(run_adb.call_count, 3)
        self.assertEqual(get_state.call_count, 12)

    def test_screenshot_recovers_again_on_each_transport_retry(self):
        failure = subprocess.CompletedProcess(
            [], 1, "", "adb.EXE: device not found"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(
                    app, "recover_adb_connection", return_value=False
                ) as recover,
                patch.object(app.time, "sleep"),
            ):
                with self.assertRaises(app.ScreenshotCaptureError):
                    self._capture(
                        tmpdir,
                        np.zeros((20, 30, 3)),
                        run_side_effect=lambda *_a, **_k: failure,
                    )

            self.assertEqual(recover.call_count, 2)

    def test_non_transport_screenshot_failure_does_not_reconnect(self):
        failure = subprocess.CompletedProcess([], 1, "", "permission denied")

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(
                    app, "recover_adb_connection", return_value=True
                ) as recover,
                patch.object(app.time, "sleep"),
            ):
                with self.assertRaises(app.ScreenshotCaptureError):
                    self._capture(tmpdir, np.zeros((20, 30, 3)), run_side_effect=lambda *_a, **_k: failure)

            recover.assert_not_called()

    def test_first_pull_failure_then_success_archives_only_successful_frame(self):
        success = subprocess.CompletedProcess([], 0, "", "")
        pull_failure = subprocess.CompletedProcess(
            [], 1, "", "temporary pull failure"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            image = np.full((20, 30, 3), 211, dtype="uint8")
            pull_attempts = {"count": 0}

            def run_adb(args, **_kwargs):
                if "pull" not in args:
                    return success
                pull_attempts["count"] += 1
                if pull_attempts["count"] == 1:
                    return pull_failure
                self.assertTrue(cv2.imwrite(str(Path(args[-1])), image))
                return success

            with patch.object(app.time, "sleep") as sleep:
                target, archive_dir, returned, run = self._capture(
                    tmpdir,
                    image,
                    run_side_effect=run_adb,
                )

            self.assertEqual(returned, target)
            self.assertEqual(run.call_count, 4)
            sleep.assert_called_once_with(app.SCREENSHOT_RETRY_INTERVAL)
            archives = list(archive_dir.iterdir())
            self.assertEqual(len(archives), 1)
            self.assertRegex(
                archives[0].name,
                self.CAPTURE_ARCHIVE_NAME_PATTERN,
            )
            np.testing.assert_array_equal(cv2.imread(str(target)), image)
            np.testing.assert_array_equal(cv2.imread(str(archives[0])), image)

    def test_all_pull_failures_add_no_archive_and_preserve_existing_files(self):
        screencap_success = subprocess.CompletedProcess([], 0, "", "")
        pull_failure = subprocess.CompletedProcess([], 1, "", "pull failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "current.png"
            archive_dir = root / "farm_screenshots"
            archive_dir.mkdir()
            old_current = np.full((20, 30, 3), 50, dtype="uint8")
            old_archived = np.full((20, 30, 3), 80, dtype="uint8")
            old_archive = (
                archive_dir
                / "device_capture_20200101_000000_000000_deadbeef.png"
            )
            self.assertTrue(cv2.imwrite(str(target), old_current))
            self.assertTrue(cv2.imwrite(str(old_archive), old_archived))
            os.utime(old_archive, (1, 1))

            def run_adb(args, **_kwargs):
                return pull_failure if "pull" in args else screencap_success

            with (
                patch.object(
                    app,
                    "SCREENSHOT_ARCHIVE_DIR",
                    archive_dir,
                    create=True,
                ),
                patch.object(
                    app.subprocess,
                    "run",
                    side_effect=run_adb,
                ) as run,
                patch.object(app.time, "sleep") as sleep,
            ):
                with self.assertRaises(app.ScreenshotCaptureError):
                    app.screenshot(target, archive_label="capture")

            self.assertEqual(
                run.call_count,
                app.SCREENSHOT_MAX_ATTEMPTS * 2,
            )
            self.assertEqual(
                sleep.call_count,
                app.SCREENSHOT_MAX_ATTEMPTS - 1,
            )
            np.testing.assert_array_equal(cv2.imread(str(target)), old_current)
            self.assertEqual(list(archive_dir.iterdir()), [old_archive])
            np.testing.assert_array_equal(
                cv2.imread(str(old_archive)),
                old_archived,
            )


class FarmEntryScreenshotTests(unittest.TestCase):
    def _run_farm_entry(self, click_results, *, farm_entry_error=None):
        events = []
        remaining_results = iter(click_results)

        def record_screenshot(path, *, archive_label="capture"):
            events.append(("screenshot", archive_label))
            if archive_label == "farm_entry" and farm_entry_error is not None:
                raise farm_entry_error
            return Path(path)

        def record_click(*_args, **_kwargs):
            events.append(("press_first_control",))
            return next(remaining_results, False)

        def record_sleep(seconds):
            events.append(("sleep", seconds))

        output = StringIO()
        with (
            patch.object(
                app,
                "screenshot",
                side_effect=record_screenshot,
            ),
            patch.object(
                app,
                "press_first_control",
                side_effect=record_click,
            ),
            patch.object(app.time, "sleep", side_effect=record_sleep),
            # 稳定检测读不到帧时会立即跳过，保证事件序列确定
            patch.object(app.cv2, "imread", return_value=None),
            redirect_stdout(output),
        ):
            result = app.open_farm()

        return result, events, output.getvalue()

    def test_first_success_captures_farm_entry_after_40_second_wait(self):
        result, events, _ = self._run_farm_entry([True])

        self.assertTrue(result)
        self.assertEqual(
            events,
            [
                ("screenshot", "capture"),
                ("press_first_control",),
                ("sleep", 40),
                ("screenshot", "scene_stable"),
                ("screenshot", "farm_entry"),
            ],
        )
        self.assertNotIn(("sleep", 5), events)

    def test_second_success_adds_one_post_wait_farm_entry_capture(self):
        result, events, _ = self._run_farm_entry([False, True])

        self.assertTrue(result)
        self.assertEqual(
            events,
            [
                ("screenshot", "capture"),
                ("press_first_control",),
                ("sleep", 5),
                ("screenshot", "capture"),
                ("press_first_control",),
                ("sleep", 40),
                ("screenshot", "scene_stable"),
                ("screenshot", "farm_entry"),
            ],
        )
        self.assertEqual(
            [event[1] for event in events if event[0] == "screenshot"],
            ["capture", "capture", "scene_stable", "farm_entry"],
        )
        self.assertEqual(
            [event[1] for event in events if event[0] == "sleep"],
            [5, 40],
        )

    def test_ten_failures_keep_capture_and_five_second_retry_behavior(self):
        result, events, _ = self._run_farm_entry([False] * 10)

        self.assertFalse(result)
        expected_events = []
        for _ in range(10):
            expected_events.extend(
                [
                    ("screenshot", "capture"),
                    ("press_first_control",),
                    ("sleep", 5),
                ]
            )
        self.assertEqual(events, expected_events)
        self.assertEqual(
            [event[1] for event in events if event[0] == "screenshot"],
            ["capture"] * 10,
        )
        self.assertEqual(
            [event[1] for event in events if event[0] == "sleep"],
            [5] * 10,
        )

    def test_post_wait_capture_failure_warns_but_keeps_success(self):
        error = app.ScreenshotCaptureError("post-wait capture failed")

        result, events, output = self._run_farm_entry(
            [True],
            farm_entry_error=error,
        )

        self.assertTrue(result)
        self.assertIn("进入农场后的截图失败", output)
        self.assertEqual(
            events,
            [
                ("screenshot", "capture"),
                ("press_first_control",),
                ("sleep", 40),
                ("screenshot", "scene_stable"),
                ("screenshot", "farm_entry"),
            ],
        )
        self.assertEqual(
            sum(event[0] == "press_first_control" for event in events),
            1,
        )


if __name__ == "__main__":
    unittest.main()
