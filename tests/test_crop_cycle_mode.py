import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
w = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class CropCycleModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'cycle.json'
        self.patcher = patch.object(w, 'CYCLE_FILE', str(self.path))
        self.patcher.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.patcher.stop)

    def first(self, cycle, day, crop='西瓜'):
        w.clear_crop_cycle()
        return w.select_crop_cycle(crop, cycle*11/12,
                                   now=datetime(2026, 10, 23)+timedelta(days=day), force_new=True)

    def test_mode_not_largest_and_later_watering_does_not_vote(self):
        self.first(1920,0)
        self.first(480,1)
        self.assertEqual(self.first(480,2)['cycle_min'],480)
        for remaining in (1753, 50, 440, 900):
            self.assertEqual(w.select_crop_cycle('西瓜', remaining)['cycle_min'],480)
        self.assertEqual(w.load_crop_record()['cycle_counts'],{'1920':1,'480':2})

    def test_tie_keeps_previous_mode(self):
        self.first(480,0)
        self.assertEqual(self.first(1920,1)['cycle_min'],480)

    def test_legacy_maximum_is_not_invented_as_a_sample(self):
        w.save_crop_record('西瓜',1920)
        self.assertEqual(w.load_crop_record()['cycle_counts'],{})
        self.assertEqual(self.first(480,0)['cycle_min'],480)
        self.assertEqual(w.load_crop_record()['cycle_counts'],{'480':1})

    def test_same_first_watering_timestamp_is_counted_once(self):
        for _ in range(3):
            w.select_crop_cycle('西瓜',440,now=datetime(2026,10,23),force_new=True)
        self.assertEqual(w.load_crop_record()['cycle_counts'],{'480':1})

    def test_reloading_and_other_crops_preserve_counts(self):
        self.first(480,0)
        self.first(960,1,crop='柠檬')
        self.first(480,2,crop='变异西瓜')
        self.assertEqual(w.load_crop_record('西瓜')['cycle_counts'],{'480':2})
        self.assertEqual(w.load_crop_record('柠檬')['cycle_counts'],{'960':1})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['crops']['西瓜']['cycle_counts'],{'480':2})

    def test_diagnostics_and_unreadable_name_do_not_add_votes(self):
        self.first(960,0)
        w.select_crop_cycle(None,20)
        w.save_crop_record('西瓜',1920,last_remaining_min=10)
        self.assertEqual(w.load_crop_record()['cycle_counts'],{'960':1})
        self.assertEqual(w.load_crop_record()['cycle_min'],960)

    def test_corrupt_counts_are_ignored_and_mode_is_loaded(self):
        self.path.write_text(json.dumps({'schema_version':3,'current_crop_name':'西瓜','crops':{
            '西瓜':{'cycle_min':1920,'cycle_counts':{'480':3,'960':2,'1920':True,'60':-1,'5':999}}}}),encoding='utf-8')
        record=w.load_crop_record()
        self.assertEqual(record['cycle_min'],480)
        self.assertEqual(record['cycle_counts'],{'480':3,'960':2})

    def test_nonfirst_observation_without_history_does_not_vote(self):
        w.select_crop_cycle('西瓜',20)
        self.assertEqual(w.load_crop_record()['cycle_counts'],{})


if __name__=='__main__':
    unittest.main()
