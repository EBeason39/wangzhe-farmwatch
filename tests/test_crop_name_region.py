"""Crop-card header OCR must not depend on the mutation fraction being recognized."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
w = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
def line(text, x=10, y=10, width=80, confidence=.99):
    return ([[x,y],[x+width,y],[x+width,y+24],[x,y+24]],text,confidence)


class CropNameRegionTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720,1280,3),dtype=np.uint8)

    def state(self, engine):
        with patch.object(w.cv2,'imread',return_value=self.image),patch.object(w,'get_ocr',return_value=engine):
            return w.read_crop_state('screen.png')

    def test_without_fraction_or_known_name_list(self):
        self.assertNotIn('狐尾草',w.KNOWN_CROP_NAMES)
        engine=Mock(side_effect=[([line('狐尾草'),line('13:16成熟')],None),([line('狐尾草')],None)])
        state=self.state(engine)
        self.assertEqual(state['crop_name'],'狐尾草')
        self.assertEqual(state['maturity_time'],(13,16,None))
        self.assertEqual(engine.call_count,2)
        self.assertEqual(engine.call_args_list[0].args[0].shape[:2],(540,640))
        self.assertEqual(engine.call_args_list[1].args[0].shape[:2],(108,576))

    def test_header_takes_priority_over_other_crop_in_broad_text(self):
        engine=Mock(side_effect=[([line('西瓜'),line('13:16成熟')],None),([line('狐尾草')],None)])
        self.assertEqual(self.state(engine)['crop_name'],'狐尾草')

    def test_split_name_fragments_and_variant_badge(self):
        rows=[line('变异',0),line('草',130,width=20),line('狐',75,width=20),line('尾',103,width=20)]
        with patch.object(w,'get_ocr',return_value=lambda _: (rows,None)):
            self.assertEqual(w.read_crop_name_region(self.image),'狐尾草')

    def test_combined_variant_name_is_normalized(self):
        with patch.object(w,'get_ocr',return_value=lambda _: ([line('变异狐尾草')],None)):
            self.assertEqual(w.read_crop_name_region(self.image),'狐尾草')

    def test_arbitrary_new_crop_is_supported(self):
        with patch.object(w,'get_ocr',return_value=lambda _: ([line('晨曦铃兰')],None)):
            self.assertEqual(w.read_crop_name_region(self.image),'晨曦铃兰')

    def test_blank_header_uses_legacy_fallback(self):
        engine=Mock(side_effect=[([line('西瓜'),line('13:16成熟')],None),([],None)])
        self.assertEqual(self.state(engine)['crop_name'],'西瓜')

    def test_header_exception_uses_legacy_fallback(self):
        engine=Mock(side_effect=[([line('西瓜'),line('13:16成熟')],None),RuntimeError('test header failure')])
        self.assertEqual(self.state(engine)['crop_name'],'西瓜')

    def test_broad_exception_does_not_prevent_header_read(self):
        engine=Mock(side_effect=[RuntimeError('test broad failure'),([line('狐尾草')],None)])
        state=self.state(engine)
        self.assertEqual(state['crop_name'],'狐尾草')
        self.assertIsNone(state['maturity_time'])
        self.assertFalse(state['is_mature'])

    def test_rejects_ui_numeric_and_low_confidence_candidates(self):
        for text,confidence in [('变异',.99),('2/3',.99),('13小时16分钟后成熟',.99),('巨大化',.99),('作物',.99),('狐尾草',.2)]:
            with self.subTest(text=text),patch.object(w,'get_ocr',return_value=lambda _: ([line(text,confidence=confidence)],None)):
                self.assertIsNone(w.read_crop_name_region(self.image))

    def test_different_rows_are_not_joined_into_crop_name(self):
        with patch.object(w,'get_ocr',return_value=lambda _: ([line('狐尾草'),line('其他内容',y=70)],None)):
            self.assertIsNone(w.read_crop_name_region(self.image))

    def test_proportional_roi_and_threefold_upscaling(self):
        engine=Mock(return_value=([line('狐尾草')],None))
        with patch.object(w,'get_ocr',return_value=engine):
            w.read_crop_name_region(np.zeros((1440,2560,3),dtype=np.uint8))
        self.assertEqual(engine.call_args.args[0].shape[:2],(216,1152))

    def test_empty_image_does_not_call_ocr(self):
        with patch.object(w,'get_ocr') as engine:
            self.assertIsNone(w.read_crop_name_region(None))
            self.assertIsNone(w.read_crop_name_region(np.zeros((0,0,3),dtype=np.uint8)))
        engine.assert_not_called()

    def test_real_header_pixels_without_any_fraction(self):
        # Real device header pixels, with every other part of the screen blanked.
        header=cv2.imread(str(Path(__file__).parent/'fixtures/fox_crop_name_header.png'))
        self.assertIsNotNone(header)
        screen=self.image.copy()
        screen[118:154,19:211]=header
        self.assertEqual(w.read_crop_name_region(screen),'狐尾草')


if __name__=='__main__':
    unittest.main()
