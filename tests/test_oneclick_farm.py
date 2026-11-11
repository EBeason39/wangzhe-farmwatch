import subprocess
import unittest
from unittest.mock import patch
from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
w = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class OneclickFarmTests(unittest.TestCase):
    def run_click(self, current, *, rc=0, error=None):
        initial={"x":855,"y":413,"score":.91}
        done=subprocess.CompletedProcess([],rc,"","rejected" if rc else "")
        with patch.object(w,"screenshot") as shot, patch.object(w,"locate_farm_action",side_effect=[initial,current]), patch.object(w,"adb_shell_result",return_value=done,side_effect=error) as adb, patch.object(w.time,"sleep"):
            result=w.perform_farm_action()
        return result,shot,adb

    def test_button_disappearing_before_click_is_failure(self):
        result,shot,adb=self.run_click(None)
        self.assertFalse(result); self.assertEqual(shot.call_count,2); adb.assert_not_called()

    def test_new_position_is_used_after_wait(self):
        result,shot,adb=self.run_click({"x":870,"y":420})
        self.assertTrue(result); self.assertEqual(shot.call_count,2)
        adb.assert_called_once_with("input tap 870 420")

    def test_adb_nonzero_exit_is_failure(self):
        result,_,_=self.run_click({"x":870,"y":420},rc=1)
        self.assertFalse(result)

    def test_adb_timeout_is_failure(self):
        result,_,_=self.run_click({"x":870,"y":420},error=subprocess.TimeoutExpired(["adb"],15))
        self.assertFalse(result)

    def test_initial_missing_button_is_not_clicked(self):
        with patch.object(w,"screenshot"),patch.object(w,"locate_farm_action",return_value=None),patch.object(w,"adb_shell_result") as adb:
            self.assertFalse(w.perform_farm_action())
        adb.assert_not_called()

    def test_no_success_notification_without_crop_or_harvest(self):
        from contextlib import ExitStack
        class StopRound(Exception): pass
        with ExitStack() as stack:
            for name,value in {"ensure_emulator_running":True,"check_adb_connection":True,"detect_resolution":(1280,720),"launch_game":True,"wait_for_game_update":True,"ensure_account_ready":True,"enter_game":True,"open_farm":True,"approach_farm_control":True,"perform_farm_action":True,"collect_harvest_result":(False,False),"inspect_crop":(None,None,None,False),"schedule_next_round":w.datetime.now()}.items():
                stack.enter_context(patch.object(w,name,return_value=value))
            for name in ("wake_screen","report_device_state","dismiss_startup_overlays","dismiss_lobby_overlays","wait_until_target_time"):
                stack.enter_context(patch.object(w,name))
            notify=stack.enter_context(patch.object(w,"notify_round"))
            stack.enter_context(patch.object(w,"wait_for_next_round",side_effect=StopRound))
            with self.assertRaises(StopRound): w.main()
            notify.assert_not_called()
