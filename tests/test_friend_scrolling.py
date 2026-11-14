"""Stable row identities and bounded, non-redundant friend-list navigation."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from farmwatch.automation import friends as f
from tests.support import make_app


DOWN = "input swipe 1020 150 1020 590 350"
UP = "input swipe 1020 554 1020 190 550"


def friend(word="ALPHA", *, name=None, background=0, y=127, pinned=True, available=False):
    x = np.arange(252, dtype=np.float32)[None, :]
    row = np.arange(36, dtype=np.float32)[:, None]
    image = np.broadcast_to(45 + 12*np.sin(x/15) + 6*np.cos(row/5), (36, 252)).copy()
    if background:
        image += 26*np.sin(x/60 + 1) + 10*np.cos(row/18)
    image = np.clip(image, 0, 255).astype(np.uint8)
    cv2.putText(image, word, (5, 27), cv2.FONT_HERSHEY_SIMPLEX, .85, 245, 2, cv2.LINE_AA)
    identity = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    return f.Friend(y, pinned, available, identity, word if name is None else name)


class StableFriendIdentityTests(unittest.TestCase):
    def test_background_changes_and_ocr_typo_do_not_create_a_new_friend(self):
        before = friend(name="ALPHA")
        after = friend(name="ALPHAx", background=1)
        raw = cv2.matchTemplate(before.identity, after.identity, cv2.TM_CCOEFF_NORMED)[0, 0]
        self.assertLess(float(raw), .985)
        self.assertTrue(f.Vision.same_friend(before, after))
        self.assertTrue(f.Vision.same_friend(after, before))

    def test_neighbor_names_are_not_fuzzily_merged(self):
        for a, b in (("ALPHA", "ALPHB"), ("ALPHAX", "ALPHAY"), ("ALPHA01", "ALPHA02")):
            with self.subTest(a=a, b=b):
                self.assertFalse(f.Vision.same_friend(friend(a), friend(b, background=1)))

    def test_weak_background_texture_is_not_a_name_identity(self):
        before = friend(name="")
        after = friend(name="")
        x = np.arange(252, dtype=np.uint8)[None, :]
        before.identity = np.tile(x, (36, 1))
        after.identity = cv2.normalize(cv2.GaussianBlur(before.identity, (0, 0), 15), None, 0, 255, cv2.NORM_MINMAX)
        after.identity = np.fliplr(after.identity).copy()
        self.assertFalse(f.Vision.same_friend(before, after))

    def test_empty_or_changed_list_positions_are_not_treated_as_the_top(self):
        self.assertFalse(f.Vision.same_list_position([], []))
        self.assertFalse(f.Vision.same_list_position([friend()], [friend(y=233)]))
        self.assertFalse(f.Vision.same_list_position([friend()], [friend(), friend("BRAVO", y=233)]))
        self.assertFalse(f.Vision.same_list_position([friend()], [friend("BRAVO")]))

    def test_same_visible_rows_survive_small_layout_and_background_changes(self):
        self.assertTrue(f.Vision.same_list_position(
            [friend(name="ALPHA")], [friend(name="ALPHAx", background=1, y=128)]
        ))


class FriendListNavigationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.stack.enter_context(patch.object(f, "take_blocked_until", return_value=None))
        self.stack.enter_context(patch.object(f.time, "sleep"))
        self.host = SimpleNamespace(
            datetime=datetime, TEMPLATE_DIR=make_app().TEMPLATE_DIR, DATA_DIR=Path(directory),
            MUMU_NAME="test", SCREENSHOT_PATH=str(Path(directory)/"screen.png"),
            get_ocr=Mock(return_value=Mock()), screenshot=Mock(),
            adb_shell_result=Mock(return_value=subprocess.CompletedProcess([], 0)),
        )
        self.runner = f.Runner(self.host, datetime.now() + timedelta(minutes=20))
        self.runner.capture = Mock(return_value=np.zeros((720, 1280, 3), np.uint8))
        self.runner.tap = Mock()
        self.runner.vision = Mock()
        self.runner.vision.same_friend.side_effect = f.Vision.same_friend
        self.runner.vision.panel_open.return_value = True

    def test_already_top_stops_after_one_boundary_probe_not_three_swipes(self):
        self.runner.vision.friends.return_value = [friend()]
        self.runner.open_social()
        self.host.adb_shell_result.assert_called_once_with(DOWN)

    def test_remembered_lower_page_rewinds_until_rows_stop_moving(self):
        self.runner.vision.friends.side_effect = [[friend("BRAVO")], [friend()], [friend()]]
        self.runner.open_social()
        self.assertEqual(self.host.adb_shell_result.call_args_list, [call(DOWN), call(DOWN)])

    def test_rewind_is_bounded_and_an_unconfirmed_top_is_not_a_complete_scan(self):
        self.runner.vision.friends.side_effect = [[friend(y=127 + i*10)] for i in range(13)]
        with self.assertRaisesRegex(f.StopVisit, "回顶未能确认"):
            self.runner.open_social()
        self.assertEqual(self.host.adb_shell_result.call_count, 12)

    def test_rewind_adb_failure_stops_immediately(self):
        self.runner.vision.friends.return_value = [friend()]
        self.host.adb_shell_result.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(f.StopVisit, "滚动失败"):
            self.runner.open_social()
        self.host.adb_shell_result.assert_called_once_with(DOWN)

    def test_first_non_pinned_row_ends_scan_without_an_extra_downward_page(self):
        pinned = friend()
        normal = friend("BRAVO", y=233, pinned=False)
        self.runner.open_social = Mock()
        self.runner.vision.friends.return_value = [pinned, normal]
        self.assertEqual(list(self.runner.scan_friends()), [[pinned, normal]])
        self.host.adb_shell_result.assert_not_called()

    def test_all_pinned_page_continues_to_find_lower_pinned_accounts(self):
        first = friend()
        lower = friend("BRAVO")
        normal = friend("CHARLIE", y=233, pinned=False)
        self.runner.open_social = Mock()
        self.runner.vision.friends.side_effect = [[first], [lower, normal]]
        self.assertEqual(list(self.runner.scan_friends()), [[first], [lower, normal]])
        self.host.adb_shell_result.assert_called_once_with(UP)

    def test_uncertain_middle_row_does_not_hide_lower_pinned_friends(self):
        first_page = [friend(), friend("BRAVO", y=233, pinned=False), friend("CHARLIE", y=339)]
        last_page = [friend("DELTA"), friend("ECHO", y=233, pinned=False)]
        self.runner.open_social = Mock()
        self.runner.vision.friends.side_effect = [first_page, last_page]
        self.assertEqual(list(self.runner.scan_friends()), [first_page, last_page])
        self.host.adb_shell_result.assert_called_once_with(UP)

    def test_same_rows_end_scan_even_if_the_farm_background_keeps_changing(self):
        self.runner.open_social = Mock()
        self.runner.vision.friends.side_effect = [[friend(name="ALPHA")], [friend(name="ALPHAx", background=1)]]
        self.assertEqual(len(list(self.runner.scan_friends())), 2)
        self.host.adb_shell_result.assert_called_once_with(UP)

    def test_visible_target_uses_fresh_coordinates_without_scanning_twice(self):
        previous = friend(available=True)
        current = friend(available=True, y=233)
        self.runner.scan_friends = Mock(return_value=iter([[previous]]))
        self.runner.vision.friends.return_value = [current]
        self.assertIs(self.runner.next_friend(), current)
        self.runner.scan_friends.assert_called_once_with()

    def test_hidden_target_is_rescanned_and_not_clicked_using_stale_coordinates(self):
        previous = friend(available=True)
        current = friend(available=True, y=233)
        self.runner.scan_friends = Mock(side_effect=[iter([[previous]]), iter([[current]])])
        self.runner.vision.friends.side_effect = [[], [current]]
        self.assertIs(self.runner.next_friend(), current)
        self.assertEqual(self.runner.scan_friends.call_count, 2)
        self.runner.tap.assert_not_called()

    def test_background_shift_does_not_reschedule_an_already_visited_friend(self):
        visited = friend(name="ALPHA")
        current = friend(name="ALPHAx", background=1, available=True)
        self.runner.visited = [visited]
        self.runner.scan_friends = Mock(return_value=iter([[current]]))
        self.assertIsNone(self.runner.next_friend())
        self.runner.tap.assert_not_called()

    def test_removed_pinning_does_not_return_a_stale_target(self):
        previous = friend(available=True)
        normal = friend(available=True, pinned=False)
        self.runner.scan_friends = Mock(side_effect=[iter([[previous]]), iter([[normal]])])
        self.runner.vision.friends.return_value = [normal]
        with self.assertRaisesRegex(f.StopVisit, "重新定位失败"):
            self.runner.next_friend()
        self.runner.tap.assert_not_called()
