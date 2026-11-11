"""Regressions for login buttons obscured by the game's age-rating badge."""
from unittest.mock import patch
import unittest

import cv2
import numpy as np

from farmwatch.config import ROOT
from tests.support import make_app


class LiveLoginControlTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.fixture = ROOT/'tests/fixtures/current/account-choices.png'

    def test_obscured_android_label_still_selects_rightmost_qq_control(self):
        with patch.object(self.app, 'get_ocr') as ocr:
            match = self.app.locate_login_control('account.android', str(self.fixture))
        self.assertIsNotNone(match)
        self.assertTrue(950 <= match['x'] <= 1005 and 540 <= match['y'] <= 595)
        ocr.assert_not_called()

    def test_ios_qq_and_android_wechat_do_not_substitute_for_android_qq(self):
        frame = cv2.imread(str(self.fixture))
        # Keep the other three real login buttons, hiding only the rightmost one.
        frame[500:630, 920:1250] = 0
        with patch.object(self.app, '_read_image', return_value=frame):
            self.assertIsNone(self.app.locate_login_control('account.android', 'no-android-qq.png'))

    def test_qq_glyph_outside_the_login_row_is_rejected(self):
        glyph = cv2.imread(str(self.app.TEMPLATE_DIR/'account-qq-android.png'))
        frame = np.zeros((720,1280,3), np.uint8)
        height,width = glyph.shape[:2]
        frame[200:200+height,960:960+width] = glyph
        with patch.object(self.app, '_read_image', return_value=frame):
            self.assertIsNone(self.app.locate_login_control('account.android', 'wrong-row.png'))

    def test_privacy_transition_uses_real_occluded_login_screen(self):
        state = {'page': 'privacy'}
        clock = {'now': 0.0}
        labels = []
        def detect(name, path):
            if state['page'] == 'privacy' and name == 'account.privacy':
                return {'x':764, 'y':546, 'score':.98}
            if state['page'] == 'choices' and name == 'account.android':
                return self.app.locate_login_control(name, str(self.fixture))
            if state['page'] == 'start' and name == 'session.start':
                return {'x':640, 'y':558, 'score':.98}
            return None
        def tap(x, y, label):
            labels.append(label)
            state['page'] = 'choices' if state['page'] == 'privacy' else 'start'
        result = self.app.ensure_account_ready(
            screenshot_fn=lambda path:path, detect_fn=detect, tap_fn=tap,
            sleep_fn=lambda value:clock.update(now=clock['now']+value),
            monotonic_fn=lambda:clock['now'], signature_fn=lambda path:state['page'],
            diagnostic_fn=lambda state,path:None, timeout_seconds=10, transition_timeout=.2,
            poll_interval=.1,
        )
        self.assertTrue(result)
        self.assertEqual(labels, ['隐私协议同意', 'QQ 安卓登录'])
