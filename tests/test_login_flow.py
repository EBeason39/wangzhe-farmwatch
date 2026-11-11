import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class LoginFlowTests(unittest.TestCase):
    """可注入的掉线登录流程行为测试。

    这些测试使用一个可变的页面名模拟截图内容；所有设备交互都通过
    注入回调完成，因此不会读取真实截图或调用 ADB。
    """

    def _run_flow(self, initial_screen, unchanged_agree_taps=0, early_start=False):
        screen = {
            "name": initial_screen,
            "unchanged": unchanged_agree_taps,
            "capture": 0,
        }
        labels = []
        transitions = {
            "privacy": "qq_login",
            "qq_login": "qq_agree_1",
            "qq_agree_1": "qq_agree_2",
            "qq_agree_2": "start",
        }
        template_for_screen = {
            "privacy": "account.privacy",
            "qq_login": "account.android",
            "qq_agree_1": "account.authorize",
            "qq_agree_2": "account.authorize",
            "start": "session.start",
        }
        center_for_screen = {
            "privacy": (700, 500),
            "qq_login": (1100, 500),
            # The same production template is used on both authorization pages.
            # Real-device evidence distinguishes them by this vertical shift.
            "qq_agree_1": (640, 467),
            "qq_agree_2": (640, 437),
            "start": (640, 600),
        }

        def screenshot_fn(_path):
            screen["capture"] += 1
            return _path

        def detect_fn(template_name, _path):
            if template_name == template_for_screen[screen["name"]]:
                x, y = center_for_screen[screen["name"]]
                return {"x": x, "y": y, "score": 0.95}
            return None

        def tap_fn(_x, _y, label):
            labels.append(label)
            if label == "QQ 授权同意" and screen["unchanged"]:
                screen["unchanged"] -= 1
                return
            if early_start and label == "QQ 授权同意":
                screen["name"] = "start"
                return
            screen["name"] = transitions[screen["name"]]

        clock = {"now": 0.0}

        def monotonic_fn():
            return clock["now"]

        def sleep_fn(seconds):
            clock["now"] += seconds

        result = app.ensure_account_ready(
            timeout_seconds=30.0,
            poll_interval=0.1,
            transition_timeout=0.2,
            screenshot_fn=screenshot_fn,
            detect_fn=detect_fn,
            tap_fn=tap_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            # Every capture has a different full-image signature, modelling
            # animation/click-ripple noise. Semantic page evidence must decide.
            signature_fn=lambda _path: f"{screen['name']}:{screen['capture']}",
            diagnostic_fn=lambda _state, _path: None,
        )
        return result, labels

    def test_start_screen_skips_all_login_clicks(self):
        result, labels = self._run_flow("start")
        self.assertTrue(result)
        self.assertEqual(labels, [])

    def test_full_flow_clicks_privacy_qq_and_two_agrees_in_order(self):
        result, labels = self._run_flow("privacy")
        self.assertTrue(result)
        self.assertEqual(
            labels,
            ["隐私协议同意", "QQ 安卓登录", "QQ 授权同意", "QQ 授权同意"],
        )

    def test_privacy_can_be_skipped_when_login_method_is_already_visible(self):
        result, labels = self._run_flow("qq_login")
        self.assertTrue(result)
        self.assertEqual(labels, ["QQ 安卓登录", "QQ 授权同意", "QQ 授权同意"])

    def test_first_qq_agree_retries_when_hash_changes_but_center_does_not(self):
        result, labels = self._run_flow("privacy", unchanged_agree_taps=1)
        self.assertTrue(result)
        self.assertEqual(labels.count("QQ 授权同意"), 3)

    def test_two_qq_agrees_are_two_distinct_actions(self):
        result, labels = self._run_flow("privacy")
        self.assertTrue(result)
        self.assertEqual(labels[-2:], ["QQ 授权同意", "QQ 授权同意"])

    def test_start_screen_appearing_early_stops_extra_clicks(self):
        result, labels = self._run_flow("privacy", early_start=True)
        self.assertTrue(result)
        self.assertEqual(labels.count("QQ 授权同意"), 1)

    def test_transition_checks_deadline_frame_without_oversleeping(self):
        screen = {"name": "privacy", "privacy_ready_at": None, "capture": 0}
        clock = {"now": 0.0}
        sleeps = []
        labels = []
        templates = {
            "privacy": ("account.privacy", 700, 500),
            "qq_login": ("account.android", 1100, 500),
            "qq_agree_1": ("account.authorize", 640, 467),
            "qq_agree_2": ("account.authorize", 640, 437),
            "start": ("session.start", 640, 600),
        }

        def screenshot_fn(path):
            screen["capture"] += 1
            ready_at = screen["privacy_ready_at"]
            if ready_at is not None and clock["now"] >= ready_at:
                screen["name"] = "qq_login"
                screen["privacy_ready_at"] = None
            return path

        def detect_fn(template_name, _path):
            expected, x, y = templates[screen["name"]]
            if template_name == expected:
                return {"x": x, "y": y, "score": 0.95}
            return None

        def tap_fn(_x, _y, label):
            labels.append(label)
            if screen["name"] == "privacy":
                # The target appears exactly at the transition deadline.
                screen["privacy_ready_at"] = clock["now"] + 8.0
            elif screen["name"] == "qq_login":
                screen["name"] = "qq_agree_1"
            elif screen["name"] == "qq_agree_1":
                screen["name"] = "qq_agree_2"
            elif screen["name"] == "qq_agree_2":
                screen["name"] = "start"

        def sleep_fn(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        result = app.ensure_account_ready(
            timeout_seconds=30.0,
            poll_interval=3.0,
            transition_timeout=8.0,
            max_state_attempts=1,
            screenshot_fn=screenshot_fn,
            detect_fn=detect_fn,
            tap_fn=tap_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=lambda: clock["now"],
            signature_fn=lambda _path: f"frame:{screen['capture']}",
            diagnostic_fn=lambda _state, _path: None,
        )

        self.assertTrue(result)
        self.assertEqual(sleeps, [3.0, 3.0, 2.0])
        self.assertEqual(
            labels,
            ["隐私协议同意", "QQ 安卓登录", "QQ 授权同意", "QQ 授权同意"],
        )

    def test_qq_login_absorbs_target_that_arrives_after_transition_timeout(self):
        # Privacy is already skipped, so the first visible page is QQ login.
        screen = {"name": "qq_login", "capture": 0}
        late = {"active": False, "deadline": None, "boundary_seen": False}
        clock = {"now": 0.0}
        labels = []
        templates = {
            "privacy": ("account.privacy", 700, 500),
            "qq_login": ("account.android", 1100, 500),
            "qq_agree_1": ("account.authorize", 640, 467),
            "qq_agree_2": ("account.authorize", 640, 437),
            "start": ("session.start", 640, 600),
        }

        def screenshot_fn(path):
            screen["capture"] += 1
            if late["active"] and clock["now"] >= late["deadline"]:
                if late["boundary_seen"]:
                    screen["name"] = "qq_agree_1"
                    late["active"] = False
                else:
                    # The deadline frame is still the source page; the next
                    # outer-loop frame has the semantic target.
                    late["boundary_seen"] = True
            return path

        def detect_fn(template_name, _path):
            expected, x, y = templates[screen["name"]]
            if template_name == expected:
                return {"x": x, "y": y, "score": 0.95}
            return None

        def tap_fn(_x, _y, label):
            labels.append(label)
            if screen["name"] == "privacy":
                screen["name"] = "qq_login"
            elif screen["name"] == "qq_login" and not late["active"]:
                late["active"] = True
                late["deadline"] = clock["now"] + 4.0
            elif screen["name"] == "qq_agree_1":
                screen["name"] = "qq_agree_2"
            elif screen["name"] == "qq_agree_2":
                screen["name"] = "start"

        result = app.ensure_account_ready(
            timeout_seconds=30.0,
            poll_interval=2.0,
            transition_timeout=4.0,
            screenshot_fn=screenshot_fn,
            detect_fn=detect_fn,
            tap_fn=tap_fn,
            sleep_fn=lambda seconds: clock.__setitem__(
                "now", clock["now"] + seconds
            ),
            monotonic_fn=lambda: clock["now"],
            signature_fn=lambda _path: f"frame:{screen['capture']}",
            diagnostic_fn=lambda _state, _path: None,
        )

        self.assertTrue(result)
        self.assertEqual(labels.count("QQ 安卓登录"), 1)

    def test_first_qq_agree_absorbs_late_shifted_authorization_page(self):
        screen = {"name": "privacy", "capture": 0}
        late = {"active": False, "deadline": None, "boundary_seen": False}
        clock = {"now": 0.0}
        labels = []
        diagnostics = []
        templates = {
            "privacy": ("account.privacy", 700, 500),
            "qq_login": ("account.android", 1100, 500),
            "qq_agree_1": ("account.authorize", 640, 467),
            "qq_agree_2": ("account.authorize", 640, 437),
            "start": ("session.start", 640, 600),
        }

        def screenshot_fn(path):
            screen["capture"] += 1
            if late["active"] and clock["now"] >= late["deadline"]:
                if late["boundary_seen"]:
                    screen["name"] = "qq_agree_2"
                    late["active"] = False
                else:
                    late["boundary_seen"] = True
            return path

        def detect_fn(template_name, _path):
            expected, x, y = templates[screen["name"]]
            if template_name == expected:
                return {"x": x, "y": y, "score": 0.95}
            return None

        def tap_fn(_x, _y, label):
            labels.append(label)
            if screen["name"] == "privacy":
                screen["name"] = "qq_login"
            elif screen["name"] == "qq_login":
                screen["name"] = "qq_agree_1"
            elif screen["name"] == "qq_agree_1" and not late["active"]:
                late["active"] = True
                late["deadline"] = clock["now"] + 4.0
            # Keep the late second authorization page stuck so the diagnostic
            # state proves it was absorbed as QQ_AGREE_2, not retried as #1.

        result = app.ensure_account_ready(
            timeout_seconds=30.0,
            poll_interval=2.0,
            transition_timeout=4.0,
            max_state_attempts=2,
            screenshot_fn=screenshot_fn,
            detect_fn=detect_fn,
            tap_fn=tap_fn,
            sleep_fn=lambda seconds: clock.__setitem__(
                "now", clock["now"] + seconds
            ),
            monotonic_fn=lambda: clock["now"],
            signature_fn=lambda _path: f"frame:{screen['capture']}",
            diagnostic_fn=lambda state, path: diagnostics.append((state, path)),
        )

        self.assertFalse(result)
        self.assertEqual(labels.count("QQ 授权同意"), 3)
        self.assertEqual(diagnostics[-1][0], "QQ_AGREE_2")

    def test_second_qq_agree_requires_start_and_never_enters_wait_start(self):
        screen = {"name": "privacy", "capture": 0}
        labels = []
        diagnostics = []
        clock = {"now": 0.0}
        templates = {
            "privacy": ("account.privacy", 700, 500),
            "qq_login": ("account.android", 1100, 500),
            "qq_agree_1": ("account.authorize", 640, 467),
            "qq_agree_2": ("account.authorize", 640, 437),
        }

        def screenshot_fn(path):
            screen["capture"] += 1
            return path

        def detect_fn(template_name, _path):
            expected, x, y = templates[screen["name"]]
            if template_name == expected:
                return {"x": x, "y": y, "score": 0.95}
            return None

        def tap_fn(_x, _y, label):
            labels.append(label)
            if screen["name"] == "privacy":
                screen["name"] = "qq_login"
            elif screen["name"] == "qq_login":
                screen["name"] = "qq_agree_1"
            elif screen["name"] == "qq_agree_1":
                screen["name"] = "qq_agree_2"
            # The second authorization tap never reaches session.start.

        result = app.ensure_account_ready(
            timeout_seconds=10.0,
            poll_interval=0.1,
            transition_timeout=0.2,
            max_state_attempts=2,
            screenshot_fn=screenshot_fn,
            detect_fn=detect_fn,
            tap_fn=tap_fn,
            sleep_fn=lambda seconds: clock.__setitem__(
                "now", clock["now"] + seconds
            ),
            monotonic_fn=lambda: clock["now"],
            signature_fn=lambda _path: f"frame:{screen['capture']}",
            diagnostic_fn=lambda state, path: diagnostics.append((state, path)),
        )

        self.assertFalse(result)
        self.assertEqual(labels.count("QQ 授权同意"), 3)
        self.assertEqual(diagnostics[-1][0], "QQ_AGREE_2")
        self.assertNotEqual(diagnostics[-1][0], "WAIT_START")

    def test_check_state_with_isolated_qq_agree_never_taps(self):
        taps = []
        diagnostics = []
        clock = {"now": 0.0}

        def detect_fn(template_name, _path):
            if template_name == "account.authorize":
                return {"x": 640, "y": 467, "score": 0.95}
            return None

        result = app.ensure_account_ready(
            timeout_seconds=0.35,
            poll_interval=0.1,
            screenshot_fn=lambda path: path,
            detect_fn=detect_fn,
            tap_fn=lambda *args: taps.append(args),
            sleep_fn=lambda seconds: clock.__setitem__(
                "now", clock["now"] + seconds
            ),
            monotonic_fn=lambda: clock["now"],
            signature_fn=lambda _path: f"frame:{clock['now']}",
            diagnostic_fn=lambda state, path: diagnostics.append((state, path)),
        )

        self.assertFalse(result)
        self.assertEqual(taps, [])
        self.assertEqual(diagnostics[-1][0], "CHECK")

    def test_default_diagnostic_records_capture_error_without_stale_click(self):
        taps = []
        detections = []
        clock = {"now": 0.0}
        success = mock.Mock(returncode=0, stdout="", stderr="")
        pull_failure = mock.Mock(returncode=1, stdout="", stderr="pull failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            screenshot_path = root / "current.png"
            stale_image = np.full((720, 1280, 3), 127, dtype="uint8")
            self.assertTrue(cv2.imwrite(str(screenshot_path), stale_image))
            stale_bytes = screenshot_path.read_bytes()

            def run_adb(args, **_kwargs):
                return pull_failure if "pull" in args else success

            def detect_stale_privacy(template_name, path):
                detections.append((template_name, path))
                if template_name == "account.privacy" and Path(path).exists():
                    return {"x": 700, "y": 500, "score": 0.95}
                return None

            with (
                mock.patch.object(app, "SCRIPT_DIR", root),
                mock.patch.object(app, "SCREENSHOT_PATH", str(screenshot_path)),
                mock.patch.object(
                    app.subprocess, "run", side_effect=run_adb
                ),
                mock.patch.object(app.time, "sleep"),
            ):
                result = app.ensure_account_ready(
                    timeout_seconds=1.0,
                    poll_interval=0.05,
                    transition_timeout=0.1,
                    max_state_attempts=1,
                    detect_fn=detect_stale_privacy,
                    tap_fn=lambda *args: taps.append(args),
                    sleep_fn=lambda seconds: clock.__setitem__(
                        "now", clock["now"] + seconds
                    ),
                    monotonic_fn=lambda: clock["now"],
                )

            self.assertFalse(result)
            self.assertEqual(taps, [])
            self.assertEqual(detections, [])
            self.assertTrue(screenshot_path.exists())
            self.assertEqual(screenshot_path.read_bytes(), stale_bytes)

            diagnostic_dir = root / "tmp" / "login_diagnostics"
            text_files = list(diagnostic_dir.glob("*.txt"))
            stale_files = list(diagnostic_dir.glob("*last-good-stale*.png"))
            self.assertEqual(len(text_files), 1)
            self.assertEqual(len(stale_files), 1)
            diagnostic_text = text_files[0].read_text(encoding="utf-8")
            self.assertIn("CHECK", diagnostic_text)
            self.assertIn("pull failed", diagnostic_text)
            self.assertIn("last-good/stale", diagnostic_text)
            self.assertEqual(stale_files[0].read_bytes(), stale_bytes)

    def test_timeout_saves_state_diagnostic_and_returns_false(self):
        diagnostics = []
        clock = {"now": 0.0}

        result = app.ensure_account_ready(
            timeout_seconds=0.3,
            poll_interval=0.1,
            screenshot_fn=lambda path: path,
            detect_fn=lambda _template, _path: None,
            tap_fn=lambda *_args: self.fail("unknown page must not tap"),
            sleep_fn=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
            monotonic_fn=lambda: clock["now"],
            signature_fn=lambda _path: "unknown",
            diagnostic_fn=lambda state, path: diagnostics.append((state, path)),
        )
        self.assertFalse(result)
        self.assertTrue(diagnostics)

    def test_screenshot_failure_returns_false_without_tapping(self):
        labels = []

        result = app.ensure_account_ready(
            timeout_seconds=1.0,
            screenshot_fn=lambda _path: (_ for _ in ()).throw(OSError("capture")),
            detect_fn=lambda _template, _path: None,
            tap_fn=lambda *_args: labels.append(_args),
            sleep_fn=lambda _seconds: None,
            monotonic_fn=lambda: 0.0,
            signature_fn=lambda _path: None,
            diagnostic_fn=lambda _state, _path: None,
        )
        self.assertFalse(result)
        self.assertEqual(labels, [])

    def test_detect_login_template_rejects_match_outside_roi(self):
        """登录模板匹配中心落在配置 ROI 外时应被拒绝。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            screenshot_path = Path(tmpdir) / "screen.png"
            image = 255 * np.ones((720, 1280, 3), dtype="uint8")
            self.assertTrue(cv2.imwrite(str(screenshot_path), image))
            match = {"x": 100, "y": 100, "score": 0.95}
            with mock.patch.object(
                app, "locate_control", return_value=match
            ) as find_mock:
                result = app.locate_login_control(
                    "account.privacy", str(screenshot_path)
                )

            self.assertIsNone(result)
            find_mock.assert_called_once_with(
                "account.privacy", str(screenshot_path), 0.80
            )

    def test_detect_login_template_accepts_normalized_match(self):
        """1280x720 截图中位于 ROI 内的匹配应原样返回。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            screenshot_path = Path(tmpdir) / "screen.png"
            image = 255 * np.ones((720, 1280, 3), dtype="uint8")
            self.assertTrue(cv2.imwrite(str(screenshot_path), image))
            match = {"x": 768, "y": 450, "score": 0.95}
            with mock.patch.object(
                app, "locate_control", return_value=match
            ):
                result = app.locate_login_control(
                    "account.privacy", str(screenshot_path)
                )

            self.assertIs(result, match)

    def test_save_login_diagnostic_creates_timestamped_file(self):
        """诊断截图应写入临时目录并保留状态名，重复保存不覆盖旧文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            screenshot_path = root / "screen.png"
            payload = b"fake screenshot bytes"
            screenshot_path.write_bytes(payload)
            with mock.patch.object(app, "SCRIPT_DIR", root):
                app._save_login_diagnostic("WAIT_START", str(screenshot_path))
                app._save_login_diagnostic("WAIT_START", str(screenshot_path))

            diagnostic_dir = root / "tmp" / "login_diagnostics"
            files = list(diagnostic_dir.glob("*.png"))
            self.assertEqual(len(files), 2)
            self.assertTrue(all("WAIT_START" in path.name for path in files))
            self.assertTrue(all(path.read_bytes() == payload for path in files))

    def test_screen_signature_hashes_bytes_and_handles_missing_file(self):
        """截图签名应是稳定 SHA-256，无法读取的文件返回 None。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            screenshot_path = Path(tmpdir) / "screen.png"
            payload = b"signature fixture"
            screenshot_path.write_bytes(payload)

            self.assertEqual(
                app._screen_signature(str(screenshot_path)),
                hashlib.sha256(payload).hexdigest(),
            )
            self.assertIsNone(
                app._screen_signature(str(Path(tmpdir) / "missing.png"))
            )


if __name__ == "__main__":
    unittest.main()
