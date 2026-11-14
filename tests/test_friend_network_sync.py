"""Network stalls must not advance relative routes or reuse uncertain plot IDs."""
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


def frame(shift=0):
    y, x = np.indices((720, 1280))
    gray = (((x//26 + y//32) % 2)*150 + 35).astype(np.uint8)
    return np.repeat(np.roll(gray, shift, axis=1)[:, :, None], 3, axis=2)


class NetworkRunnerCase(unittest.TestCase):
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
            get_ocr=Mock(return_value=Mock()), screenshot=Mock(), reset_position=Mock(return_value=True),
            adb_shell_result=Mock(return_value=subprocess.CompletedProcess([], 0)),
        )
        self.runner = f.Runner(self.host, datetime.now() + timedelta(minutes=20))
        self.a, self.b, self.c = frame(), frame(9), frame(18)
        self.runner.capture = Mock(return_value=self.a)
        self.runner.vision = Mock()
        self.runner.vision.scene_wait_reason.return_value = None
        self.runner.vision.plot.return_value = f.PlotState(selected=True, unripe=True)
        self.runner.tap = Mock()
        identity = np.tile(np.arange(252, dtype=np.uint8), (36, 1))
        self.friend = f.Friend(127, True, True, identity, "alpha")

    def settled(self, image):
        self.runner._settled_signature = f.Vision.scene_signature(image)
        return image


class SceneStabilityTests(NetworkRunnerCase):
    def test_only_four_consecutive_stable_frames_allow_continuation(self):
        self.runner.capture.side_effect = [self.a, self.b, self.c, self.c, self.c, self.c]
        self.assertIs(self.runner.wait_scene_ready(), self.c)
        self.assertEqual(self.runner.capture.call_count, 6)
        self.host.adb_shell_result.assert_not_called()

    def test_network_prompt_invalidates_the_earlier_stability_streak(self):
        self.runner.vision.scene_wait_reason.side_effect = [None]*3 + ["正在重连"] + [None]*4
        self.assertIs(self.runner.wait_scene_ready(), self.a)
        self.assertEqual(self.runner.capture.call_count, 8)
        self.assertTrue(self.runner._wait_had_blocker)

    def test_persistent_network_freeze_is_bounded_and_sends_no_input(self):
        self.runner.vision.scene_wait_reason.return_value = "网络连接异常"
        with self.assertRaisesRegex(f.RouteSyncLost, "未恢复稳定"):
            self.runner.wait_scene_ready(timeout_s=1)
        self.assertEqual(self.runner.capture.call_count, 3)
        self.host.adb_shell_result.assert_not_called()
        self.assertIsNone(self.runner._settled_signature)

    def test_own_farm_deadline_aborts_the_wait_without_route_recovery(self):
        self.runner.deadline = f.time.monotonic() - 1
        with self.assertRaises(f.StopVisit):
            self.runner.wait_scene_ready()
        self.runner.capture.assert_not_called()

    def test_avatar_and_ui_animation_are_not_world_movement(self):
        changed = self.a.copy()
        changed[:130] = 255
        changed[:, :235] = 255
        changed[:, 975:] = 255
        changed[610:] = 255
        changed[270:570, 540:730] = 255
        np.testing.assert_array_equal(f.Vision.scene_signature(self.a), f.Vision.scene_signature(changed))

    def test_world_movement_changes_the_signature(self):
        diff = np.mean(cv2.absdiff(f.Vision.scene_signature(self.a), f.Vision.scene_signature(self.b)))
        self.assertGreater(float(diff), 4.0)

    def test_explicit_reconnect_and_loading_messages_are_recognized(self):
        vision = object.__new__(f.Vision)
        vision.at_friend = Mock(return_value=True)
        vision.panel_open = Mock(return_value=False)
        vision.read = Mock()
        for message in ("网络连接异常", "正在重新连接服务器", "与服务器连接已中断", "正在加载", "正在重连"):
            with self.subTest(message=message):
                vision.read.return_value = message
                self.assertTrue(vision.scene_wait_reason(self.a))
        for message in ("拿取成功", "未成熟", "普通角色昵称"):
            with self.subTest(message=message):
                vision.read.return_value = message
                self.assertIsNone(vision.scene_wait_reason(self.a))


class MovementSynchronizationTests(NetworkRunnerCase):
    def test_each_segment_waits_for_settling_before_the_next_swipe(self):
        self.settled(self.a)
        self.runner.capture.side_effect = [self.a, self.b]
        images = iter([self.b, self.c])
        self.runner.wait_scene_ready = Mock(side_effect=lambda: self.settled(next(images)))
        actions = Mock()
        actions.attach_mock(self.host.adb_shell_result, "adb")
        actions.attach_mock(self.runner.wait_scene_ready, "settle")
        self.runner.move([[0, 1100], [0, 1100]])
        command = "input swipe 193 507 293 507 1100"
        self.assertEqual(actions.mock_calls, [call.adb(command), call.settle(), call.adb(command), call.settle()])

    def test_frozen_or_rolled_back_first_move_does_not_send_the_second(self):
        self.settled(self.a)
        self.runner.wait_scene_ready = Mock(return_value=self.a)
        with self.assertRaisesRegex(f.RouteSyncLost, "场景未变化"):
            self.runner.move([[0, 1100], [0, 1100]])
        self.assertEqual(self.host.adb_shell_result.call_count, 1)

    def test_late_camera_rollback_before_input_is_not_accepted_as_a_new_origin(self):
        self.settled(self.a)
        self.runner.capture.return_value = self.b
        with self.assertRaisesRegex(f.RouteSyncLost, "场景位置发生变化"):
            self.runner.move([[0, 1100]])
        self.host.adb_shell_result.assert_not_called()

    def test_network_interruption_before_move_requires_reposition_after_recovery(self):
        self.settled(self.a)
        self.runner.vision.scene_wait_reason.return_value = "正在重连"
        self.runner.wait_scene_ready = Mock(return_value=self.a)
        with self.assertRaisesRegex(f.RouteSyncLost, "必须重新定位"):
            self.runner.move([[0, 1100]])
        self.host.adb_shell_result.assert_not_called()

    def test_network_prompt_during_move_stops_even_after_the_scene_recovers(self):
        self.settled(self.a)
        def reconnect():
            self.runner._wait_had_blocker = True
            return self.settled(self.b)
        self.runner.wait_scene_ready = Mock(side_effect=reconnect)
        with self.assertRaisesRegex(f.RouteSyncLost, "走位期间"):
            self.runner.move([[0, 1100], [0, 1100]])
        self.assertEqual(self.host.adb_shell_result.call_count, 1)

    def test_adb_rejection_is_a_transport_stop_not_a_blind_route_retry(self):
        self.settled(self.a)
        self.host.adb_shell_result.return_value = subprocess.CompletedProcess([], 1)
        self.runner.wait_scene_ready = Mock()
        with self.assertRaisesRegex(f.StopVisit, "ADB 走位失败"):
            self.runner.move([[0, 1100], [0, 1100]])
        self.assertEqual(self.host.adb_shell_result.call_count, 1)
        self.runner.wait_scene_ready.assert_not_called()

    def test_reset_waits_for_network_then_confirms_the_restored_scene(self):
        self.runner.wait_scene_ready = Mock()
        actions = Mock()
        actions.attach_mock(self.runner.wait_scene_ready, "settle")
        actions.attach_mock(self.host.reset_position, "reset")
        self.runner.reset_friend_origin()
        self.assertEqual(actions.mock_calls, [call.settle(), call.reset(), call.settle()])

    def test_missing_or_rejected_reset_never_continues_the_route(self):
        self.runner.wait_scene_ready = Mock()
        self.host.reset_position.return_value = False
        with self.assertRaisesRegex(f.RouteSyncLost, "复原失败"):
            self.runner.reset_friend_origin()
        self.assertEqual(self.runner.wait_scene_ready.call_count, 1)


class PlotSynchronizationTests(NetworkRunnerCase):
    def test_unselected_plot_is_route_loss_not_an_unknown_to_walk_past(self):
        self.runner.vision.plot.return_value = f.PlotState()
        self.runner.wait_scene_ready = Mock(return_value=self.a)
        with self.assertRaisesRegex(f.RouteSyncLost, "未选中"):
            self.runner.take_plot("R3-4")
        self.runner.tap.assert_not_called()

    def test_crop_panel_can_finish_loading_without_a_position_change(self):
        self.runner.vision.plot.side_effect = [f.PlotState(), f.PlotState(selected=True, unripe=True)]
        self.runner.wait_scene_ready = Mock(return_value=self.a)
        self.assertEqual(self.runner.take_plot("R3-4"), "unavailable")
        self.runner.tap.assert_not_called()

    def test_delayed_movement_while_waiting_for_crop_panel_requires_replay(self):
        self.runner.vision.plot.return_value = f.PlotState()
        self.runner.wait_scene_ready = Mock(return_value=self.b)
        with self.assertRaisesRegex(f.RouteSyncLost, "等待期间位置"):
            self.runner.take_plot("R3-4")
        self.runner.tap.assert_not_called()

    def test_reconnecting_crop_panel_requires_replay_even_without_camera_change(self):
        self.runner.vision.plot.return_value = f.PlotState()
        self.runner._wait_had_blocker = True
        self.runner.wait_scene_ready = Mock(return_value=self.a)
        with self.assertRaisesRegex(f.RouteSyncLost, "等待时网络"):
            self.runner.take_plot("R3-4")
        self.runner.tap.assert_not_called()

    def test_network_dialog_over_a_selected_crop_prevents_any_take_click(self):
        self.runner.vision.scene_wait_reason.return_value = "正在重连"
        with self.assertRaisesRegex(f.RouteSyncLost, "拿取前网络"):
            self.runner.take_plot("R3-4")
        self.runner.tap.assert_not_called()
        self.runner.vision.plot.assert_not_called()

    def test_network_dialog_after_click_prevents_a_second_click(self):
        self.runner.vision.scene_wait_reason.side_effect = [None, "网络连接异常"]
        self.runner.vision.plot.return_value = f.PlotState(selected=True, button=(1073, 569))
        with self.assertRaisesRegex(f.RouteSyncLost, "拿取中网络"):
            self.runner.take_plot("R3-4")
        self.assertEqual(self.runner.tap.call_count, 1)

    def test_position_change_before_take_sends_no_click(self):
        self.settled(self.a)
        self.runner.capture.return_value = self.b
        with self.assertRaisesRegex(f.RouteSyncLost, "拿取前"):
            self.runner.take_plot("R3-4")
        self.runner.tap.assert_not_called()
        self.runner.vision.plot.assert_not_called()

    def test_button_confirmation_rechecks_position_before_any_take_click(self):
        self.settled(self.a)
        self.runner.capture.side_effect = [self.a, self.b]
        self.runner.vision.plot.return_value = f.PlotState(selected=True, stock=44)
        with self.assertRaisesRegex(f.RouteSyncLost, "拿取前"):
            self.runner.take_plot("R3-4")
        self.runner.tap.assert_not_called()

    def test_lower_stock_on_a_different_position_is_not_counted_as_success(self):
        self.settled(self.a)
        self.runner.capture.side_effect = [self.a, self.b]
        self.runner.vision.plot.side_effect = [
            f.PlotState(selected=True, stock=44, button=(1073, 569)),
            f.PlotState(selected=True, stock=20, disabled=True),
        ]
        with self.assertRaisesRegex(f.RouteSyncLost, "拿取中"):
            self.runner.take_plot("R3-4")
        self.assertEqual(self.runner.tap.call_count, 1)
        self.assertEqual(self.runner.vision.plot.call_count, 1)

    def test_losing_crop_selection_after_click_stops_the_route(self):
        self.runner.vision.plot.side_effect = [f.PlotState(selected=True, button=(1073, 569)), f.PlotState()]
        with self.assertRaisesRegex(f.RouteSyncLost, "拿取中失去定位"):
            self.runner.take_plot("R3-4")
        self.assertEqual(self.runner.tap.call_count, 1)


class RouteRecoveryTests(NetworkRunnerCase):
    def prepare_route(self):
        self.runner.move = Mock()
        self.runner.reset_friend_origin = Mock()
        self.runner.selected_plot_state = Mock(return_value=f.PlotState(selected=True))

    def test_route_loss_restarts_from_entry_without_double_counting_the_first_take(self):
        self.prepare_route()
        failed = False
        def take(plot_id):
            nonlocal failed
            if plot_id == "L1-2" and not failed:
                failed = True
                raise f.RouteSyncLost("network delayed movement")
            return "taken"
        self.runner.take_plot = Mock(side_effect=take)
        actions = Mock()
        actions.attach_mock(self.runner.move, "move")
        actions.attach_mock(self.runner.reset_friend_origin, "reset")
        self.runner.walk(self.friend)
        self.assertEqual(actions.mock_calls[:5], [
            call.move(self.runner.route["entry"]), call.move([]),
            call.move([[180, 1100]]), call.reset(), call.move(self.runner.route["entry"]),
        ])
        visit = self.runner.report["visits"][0]
        self.assertTrue(visit["complete"])
        self.assertEqual(len(visit["plots"]), 24)
        self.assertEqual(sum(plot["outcome"] == "taken" for plot in visit["plots"]), 24)
        self.runner.selected_plot_state.assert_called_once_with("L1-1")
        self.runner.reset_friend_origin.assert_called_once_with()

    def test_two_route_losses_stop_before_any_later_plot_and_keep_safe_progress(self):
        self.prepare_route()
        def take(plot_id):
            if plot_id == "L1-2":
                raise f.RouteSyncLost("lost selection")
            return "taken"
        self.runner.take_plot = Mock(side_effect=take)
        self.runner.walk(self.friend)
        visit = self.runner.report["visits"][0]
        self.assertFalse(visit["complete"])
        self.assertEqual(len(visit["route_sync_losses"]), 2)
        self.assertEqual(self.runner.move.call_count, 6)
        self.assertEqual(self.runner.pending_for(self.friend)["taken"], {"L1-1"})
        self.runner.reset_friend_origin.assert_called_once_with()

    def test_failed_reset_never_replays_from_the_unknown_position(self):
        self.prepare_route()
        self.runner.take_plot = Mock(side_effect=f.RouteSyncLost("lost selection"))
        self.runner.reset_friend_origin.side_effect = f.RouteSyncLost("reset unavailable")
        self.runner.walk(self.friend)
        self.assertEqual(self.runner.move.call_count, 2)
        self.assertFalse(self.runner.report["visits"][0]["complete"])

    def test_deadline_and_daily_limit_stops_are_not_retried(self):
        self.prepare_route()
        self.runner.move.side_effect = f.StopVisit("own farm deadline")
        with self.assertRaises(f.StopVisit):
            self.runner.walk(self.friend)
        self.runner.reset_friend_origin.assert_not_called()

    def test_old_open_loop_progress_is_not_reused_under_new_sync_contract(self):
        self.runner.save_progress(self.friend, {"L1-1", "R3-4"})
        self.runner.limit.data["pending_visits"][0]["route_version"] = 2
        self.assertGreater(self.runner.route["version"], 2)
        self.assertEqual(self.runner.load_progress("pending_visits"), [])

    def test_old_running_worker_cannot_make_progress_trusted_by_reading_new_route_json(self):
        self.runner.save_progress(self.friend, {"R3-4"})
        entry = self.runner.limit.data["pending_visits"][0]
        self.assertEqual(entry["route_sync_version"], f.ROUTE_SYNC_VERSION)
        entry.pop("route_sync_version")  # 旧进程即使读取路线 v3 也不会写同步能力标记。
        self.assertEqual(entry["route_version"], self.runner.route["version"])
        self.assertEqual(self.runner.load_progress("pending_visits"), [])
