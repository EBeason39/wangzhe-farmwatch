import unittest
from pathlib import Path
import tempfile
from unittest.mock import call, patch

import numpy as np

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class UiRecognitionTests(unittest.TestCase):
    def test_current_start_screen_detects_button_in_bottom_center(self):
        template = app.cv2.imread(
            str(app.TEMPLATE_DIR / "session-start.png")
        )
        screenshot = np.zeros((720, 1280, 3), dtype="uint8")
        height, width = template.shape[:2]
        screenshot[536:536+height, 566:566+width] = template
        # Windows 下 NamedTemporaryFile 会一直持有独占句柄，OpenCV 写同名文件会被拒。
        with tempfile.TemporaryDirectory() as tmpdir:
            screen_path = str(Path(tmpdir) / "screen.png")
            self.assertTrue(app.cv2.imwrite(screen_path, screenshot))
            result = app.locate_start_control(screen_path)

        self.assertIsNotNone(result)
        self.assertTrue(500 <= result["x"] <= 780)
        self.assertTrue(480 <= result["y"] <= 650)

    def test_start_detection_rejects_match_outside_button_region(self):
        with (
            patch.object(
                app,
                "locate_control",
                return_value={"x": 123, "y": 180, "score": 0.9},
            ),
            patch.object(
                app.cv2,
                "imread",
                return_value=np.zeros((720, 1280, 3), dtype="uint8"),
            ),
        ):
            result = app.locate_start_control("screen.png")

        self.assertIsNone(result)

    def test_control_priority_tries_account_before_start(self):
        with (
            patch.object(
                app,
                "locate_control",
                side_effect=[None, {"x": 640, "y": 555, "score": 0.91}],
            ) as find,
            patch.object(app, "tap") as tap,
        ):
            result = app.press_first_control(
                ("account.android", "session.start"),
                "screen.png",
                0.7,
                "开始游戏",
            )

        self.assertTrue(result)
        self.assertEqual(
            find.call_args_list,
            [
                call("account.android", "screen.png", 0.7),
                call("session.start", "screen.png", 0.7),
            ],
        )
        tap.assert_called_once_with(640, 555, "开始游戏")

    def test_oneclick_detector_rejects_scene_texture_outside_roi(self):
        match = {"x": 394, "y": 385, "score": 0.62}
        with (
            patch.object(app, "locate_control", return_value=match),
            patch.object(
                app.cv2,
                "imread",
                return_value=np.zeros((720, 1280, 3), dtype=np.uint8),
            ),
        ):
            self.assertIsNone(app.locate_farm_action("screen.png"))

    def test_oneclick_detector_accepts_button_in_expected_roi(self):
        match = {"x": 855, "y": 413, "score": 0.86}
        with (
            patch.object(app, "locate_control", return_value=match),
            patch.object(
                app.cv2,
                "imread",
                return_value=np.zeros((720, 1280, 3), dtype=np.uint8),
            ),
        ):
            self.assertEqual(
                app.locate_farm_action("screen.png"), match
            )

    def test_login_start_detection_accepts_new_start_template(self):
        def detect(template_name, _path, _threshold):
            if template_name == "session.start":
                return {"x": 640, "y": 555, "score": 0.9}
            return None

        with (
            patch.object(app, "locate_control", side_effect=detect),
            patch.object(
                app.cv2,
                "imread",
                return_value=np.zeros((720, 1280, 3), dtype="uint8"),
            ),
        ):
            result = app.locate_start_control("screen.png")

        self.assertEqual(result["x"], 640)


if __name__ == "__main__":
    unittest.main()
