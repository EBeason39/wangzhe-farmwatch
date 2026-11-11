"""Real pixel recognition and conservative OCR decisions on the supported canvas."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from farmwatch.config import ROOT
from tests.support import make_app

FIXTURES = ROOT / 'tests/fixtures/current'


def word(text, x, y, score=.98):
    return ([[x-35,y-12], [x+35,y-12], [x+35,y+12], [x-35,y+12]], text, score)


class CapturedControlTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_targets_are_found_in_fresh_game_pixels(self):
        cases = {
            'session.start': ('session.png', (560, 520, 730, 590)),
            'farm.enter': ('lobby.png', (260, 520, 390, 570)),
            'dialog.close': ('overlay.png', (1160, 75, 1215, 125)),
            'farm.action': ('farm-action.png', (810, 375, 900, 465)),
            'farm.reset': ('farm-reset.png', (1160, 630, 1210, 680)),
        }
        for control, (file, rectangle) in cases.items():
            with self.subTest(control=control):
                hit = self.app.locate_control(control, str(FIXTURES/file))
                self.assertIsNotNone(hit)
                x0,y0,x1,y1 = rectangle
                self.assertTrue(x0 <= hit['x'] <= x1 and y0 <= hit['y'] <= y1)

    def test_other_scenes_do_not_produce_click_targets(self):
        cases = [('session.start', 'lobby.png'), ('farm.enter', 'farm-action.png'),
                 ('farm.action', 'farm-idle.png'), ('dialog.close', 'session.png')]
        for control, file in cases:
            with self.subTest(control=control, file=file):
                self.assertIsNone(self.app.locate_control(control, str(FIXTURES/file)))

    def test_other_canvas_sizes_are_rejected_before_matching(self):
        for dimensions in [(360,640), (1080,1920), (1280,720)]:
            with self.subTest(dimensions=dimensions), \
                 patch.object(self.app, '_read_image', return_value=np.zeros((*dimensions,3),np.uint8)), \
                 patch.object(cv2, 'matchTemplate') as match:
                self.assertIsNone(self.app.locate_control('farm.enter', 'frame.png'))
                match.assert_not_called()

    def test_missing_assets_fail_without_clicking(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.app, 'TEMPLATE_DIR', Path(directory)), patch.object(self.app,'tap') as tap:
            self.assertFalse(self.app.press_control('farm.enter', str(FIXTURES/'lobby.png')))
            tap.assert_not_called()

    def test_entry_outside_clickable_region_is_rejected(self):
        template=cv2.imread(str(self.app.TEMPLATE_DIR/'farm-enter.png'))
        frame=np.zeros((720,1280,3),np.uint8)
        height,width=template.shape[:2]
        frame[200:200+height,900:900+width]=template
        with patch.object(self.app,'_read_image',return_value=frame), patch.object(self.app,'tap') as tap:
            self.assertFalse(self.app.press_control('farm.enter','off-region.png'))
            tap.assert_not_called()

    def test_open_farm_uses_live_entry_without_retry(self):
        with patch.object(self.app,'SCREENSHOT_PATH',str(FIXTURES/'lobby.png')), \
             patch.object(self.app,'screenshot') as capture, patch.object(self.app,'tap') as tap, \
             patch.object(self.app.time,'sleep') as sleep, \
             patch.object(self.app,'wait_farm_scene_stable',return_value=True):
            self.assertTrue(self.app.open_farm())
        self.assertEqual(tap.call_count,1)
        sleep.assert_called_once_with(40)
        capture.assert_any_call(str(FIXTURES/'lobby.png'),archive_label='farm_entry')


class TextControlTests(unittest.TestCase):
    def setUp(self):
        self.app=make_app()
        self.frame=cv2.imread(str(FIXTURES/'session.png'))

    def recognize(self, control, words):
        ocr=Mock(return_value=(words,None))
        with patch.object(self.app,'_read_image',return_value=self.frame), \
             patch.object(self.app,'get_ocr',return_value=ocr):
            return self.app.locate_control(control,'synthetic-page.png')

    def test_agreement_requires_the_right_screen_context(self):
        self.assertIsNone(self.recognize('account.privacy',[word('同意',700,500)]))
        self.assertIsNone(self.recognize('account.authorize',[word('同意',700,500),word('广告',600,100)]))
        self.assertIsNotNone(self.recognize('account.privacy',[word('同意',700,500),word('隐私保护指引',600,100)]))
        self.assertIsNotNone(self.recognize('account.authorize',[word('同意',700,500),word('QQ授权登录',600,100)]))

    def test_wrong_label_position_confidence_and_ambiguity_are_rejected(self):
        context=word('QQ授权登录',600,100)
        for words in ([context,word('不同意',700,500)], [context,word('同意',50,50)],
                      [context,word('同意',700,500,.60)],
                      [context,word('同意',600,500),word('同意',800,500)]):
            with self.subTest(words=words):
                self.assertIsNone(self.recognize('account.authorize',words))

    def test_continue_is_not_mistaken_for_a_harvest(self):
        self.assertIsNone(self.recognize('harvest.continue',[word('继续',640,620)]))
        self.assertIsNotNone(self.recognize('harvest.continue',
                                          [word('点击继续',640,620),word('获得经验',640,300)]))

    def test_same_frame_reuses_ocr_between_control_queries(self):
        ocr=Mock(return_value=([word('同意',700,500),word('QQ授权登录',600,100)],None))
        with patch.object(self.app,'get_ocr',return_value=ocr):
            self.app.locate_control('account.privacy',str(FIXTURES/'session.png'))
            self.assertIsNotNone(self.app.locate_control('account.authorize',str(FIXTURES/'session.png')))
        ocr.assert_called_once()
