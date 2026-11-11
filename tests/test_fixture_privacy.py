"""Prevent complete account screens from returning to the public test corpus."""
import json
from pathlib import Path
import unittest

import cv2
import numpy as np


class PublishedFixtureTests(unittest.TestCase):
    def test_friend_images_only_contain_the_documented_regions(self):
        directory = Path(__file__).parent/'fixtures/friends'
        manifest = json.loads((directory/'masking.json').read_text(encoding='utf-8'))
        self.assertEqual({path.name for path in directory.glob('*.png')},set(manifest['files']))
        for name,entry in manifest['files'].items():
            with self.subTest(file=name):
                image = cv2.imread(str(directory/name))
                self.assertEqual(image.shape,(720,1280,3))
                allowed = np.zeros((720,1280),dtype=bool)
                for x0,y0,x1,y1 in entry['regions']:
                    self.assertTrue(0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 720)
                    allowed[y0:y1,x0:x1] = True
                self.assertFalse(np.any(image[~allowed]))
                # Owner/account header, avatar column and chat footer are never
                # needed for crop recognition or friend-row selection.
                self.assertFalse(np.any(image[:110,:640]))
                self.assertFalse(np.any(image[680:]))
                if name == 'social.png':
                    for y in [127,233,339,445,551]:
                        self.assertFalse(np.any(image[y-31:y+12,714:793]))
