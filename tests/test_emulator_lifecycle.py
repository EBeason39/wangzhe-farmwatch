import os
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import farmwatch.adapters.emulator as mumu_control
from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import Settings
app = make_app(Settings(screenshots_enabled=True, screenshots_mode="all"))
class EmulatorConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.original_device = app.DEVICE
        self.original_device_from_env = getattr(app, "_device_from_env", False)
        self.original_manage_mumu = getattr(app, "_manage_mumu", None)
        self.original_cli_path = getattr(app, "_mumu_cli_path", None)
        self.original_mumu_name = getattr(app, "MUMU_NAME", "farm")

    def tearDown(self):
        app.DEVICE = self.original_device
        app._device_from_env = self.original_device_from_env
        if self.original_manage_mumu is not None:
            app._manage_mumu = self.original_manage_mumu
        app._mumu_cli_path = self.original_cli_path
        app.MUMU_NAME = self.original_mumu_name

    def test_dynamic_device_is_updated_from_running_instance(self):
        cli = Path("mumu-cli.exe")
        with (
            patch.object(app, "_device_from_env", False),
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "DEVICE", "127.0.0.1:16704"),
            patch.object(app, "_mumu_cli_path", None),
            patch.object(mumu_control, "find_mumu_cli", return_value=cli) as find_cli,
            patch.object(
                mumu_control,
                "ensure_mumu_running",
                return_value="127.0.0.1:17777",
            ) as ensure,
            redirect_stdout(StringIO()),
        ):
            self.assertTrue(app.ensure_emulator_running())
            actual_device = app.DEVICE

        find_cli.assert_called_once_with()
        ensure.assert_called_once_with(cli, app.MUMU_NAME, app.ADB, timeout=180, stale_restart_delay=None)
        self.assertEqual(actual_device, "127.0.0.1:17777")

    def test_explicit_device_is_preserved_when_running_instance_reports_other_address(self):
        cli = Path("mumu-cli.exe")
        with (
            patch.object(app, "_device_from_env", True),
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "DEVICE", "192.0.2.10:5555"),
            patch.object(app, "_mumu_cli_path", cli),
            patch.object(mumu_control, "find_mumu_cli") as find_cli,
            patch.object(
                mumu_control,
                "ensure_mumu_running",
                return_value="127.0.0.1:17777",
            ) as ensure,
            redirect_stdout(StringIO()),
        ):
            self.assertTrue(app.ensure_emulator_running())
            actual_device = app.DEVICE

        find_cli.assert_not_called()
        ensure.assert_called_once_with(cli, app.MUMU_NAME, app.ADB, timeout=180, stale_restart_delay=None)
        self.assertEqual(actual_device, "192.0.2.10:5555")

    def test_start_failure_returns_false_and_prints_diagnostic(self):
        cli = Path("mumu-cli.exe")
        output = StringIO()
        with (
            patch.object(app, "_device_from_env", False),
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "_mumu_cli_path", cli),
            patch.object(
                mumu_control,
                "ensure_mumu_running",
                side_effect=mumu_control.MumuControlError("启动命令失败"),
            ),
            redirect_stdout(output),
        ):
            self.assertFalse(app.ensure_emulator_running())

        text = output.getvalue()
        self.assertIn("MuMu 启动失败", text)
        self.assertIn("启动命令失败", text)


class EmulatorShutdownTests(unittest.TestCase):
    def setUp(self):
        self.original_cli_path = getattr(app, "_mumu_cli_path", None)
        self.original_mumu_name = getattr(app, "MUMU_NAME", "farm")
        self.original_manage_mumu = getattr(app, "_manage_mumu", None)

    def tearDown(self):
        app._mumu_cli_path = self.original_cli_path
        app.MUMU_NAME = self.original_mumu_name
        if self.original_manage_mumu is not None:
            app._manage_mumu = self.original_manage_mumu

    def test_shutdown_failure_returns_false_and_prints_wait_diagnostic(self):
        cli = Path("mumu-cli.exe")
        output = StringIO()
        with (
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "_mumu_cli_path", cli),
            patch.object(
                mumu_control,
                "shutdown_mumu",
                side_effect=mumu_control.MumuControlError("关闭命令失败"),
            ),
            redirect_stdout(output),
        ):
            self.assertFalse(app.shutdown_emulator_for_wait())

        text = output.getvalue()
        self.assertIn("将继续等待", text)
        self.assertIn("关闭命令失败", text)


class EmulatorManagementCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.original_device = app.DEVICE
        self.original_device_from_env = app._device_from_env
        self.original_manage_mumu = app._manage_mumu
        self.original_cli_path = app._mumu_cli_path

    def tearDown(self):
        app.DEVICE = self.original_device
        app._device_from_env = self.original_device_from_env
        app._manage_mumu = self.original_manage_mumu
        app._mumu_cli_path = self.original_cli_path

    def test_explicit_device_skips_mumu_management_and_main_reaches_adb(self):
        output = StringIO()
        with (
            patch.object(app, "_device_from_env", True),
            patch.object(app, "_manage_mumu", False),
            patch.object(app, "_mumu_cli_path", None),
            patch.object(mumu_control, "find_mumu_cli") as find_cli,
            patch.object(mumu_control, "ensure_mumu_running") as ensure,
            redirect_stdout(output),
        ):
            self.assertTrue(app.ensure_emulator_running())

        find_cli.assert_not_called()
        ensure.assert_not_called()
        self.assertIn("显式 FARMWATCH_DEVICE，跳过 MuMu 管理", output.getvalue())

        main_output = StringIO()
        with (
            patch.object(app, "_device_from_env", True),
            patch.object(app, "_manage_mumu", False),
            patch.object(app, "check_adb_connection", return_value=False) as check_adb,
            redirect_stdout(main_output),
        ):
            app.main()

        check_adb.assert_called_once_with()
        self.assertIn("使用外部 ADB", main_output.getvalue())

    def test_manual_management_disable_uses_distinct_skip_log(self):
        output = StringIO()
        with (
            patch.object(app, "_device_from_env", False),
            patch.object(app, "_manage_mumu", False),
            patch.object(app, "_platform_is_windows", True),
            patch.object(app, "_manage_mumu_configured_value", "0"),
            patch.object(mumu_control, "find_mumu_cli") as find_cli,
            patch.object(mumu_control, "ensure_mumu_running") as ensure,
            redirect_stdout(output),
        ):
            self.assertTrue(app.ensure_emulator_running())

        find_cli.assert_not_called()
        ensure.assert_not_called()
        text = output.getvalue()
        self.assertIn("MuMu 自动管理已禁用", text)
        self.assertIn("FARMWATCH_MANAGE_MUMU=0", text)
        self.assertNotIn("显式 FARMWATCH_DEVICE", text)

    def test_non_windows_default_disable_has_platform_reason_not_external_prompt(self):
        output = StringIO()
        with (
            patch.object(app, "_device_from_env", False),
            patch.object(app, "_manage_mumu", False),
            patch.object(app, "_platform_is_windows", False),
            patch.object(app, "_manage_mumu_configured_value", None),
            patch.object(mumu_control, "find_mumu_cli") as find_cli,
            patch.object(mumu_control, "ensure_mumu_running") as ensure,
            redirect_stdout(output),
        ):
            self.assertTrue(app.ensure_emulator_running())

        find_cli.assert_not_called()
        ensure.assert_not_called()
        self.assertIn("非 Windows", output.getvalue())

        main_output = StringIO()
        with (
            patch.object(app, "_device_from_env", False),
            patch.object(app, "_manage_mumu", False),
            patch.object(app, "_platform_is_windows", False),
            patch.object(app, "_manage_mumu_configured_value", None),
            patch.object(app, "check_adb_connection", return_value=False),
            redirect_stdout(main_output),
        ):
            app.main()

        self.assertIn("非 Windows", main_output.getvalue())
        self.assertIn("使用 ADB", main_output.getvalue())
        self.assertNotIn("使用外部 ADB", main_output.getvalue())

    def test_explicit_device_skips_shutdown_management(self):
        output = StringIO()
        with (
            patch.object(app, "_device_from_env", True),
            patch.object(app, "_manage_mumu", False),
            patch.object(mumu_control, "shutdown_mumu") as shutdown,
            redirect_stdout(output),
        ):
            self.assertTrue(app.shutdown_emulator_for_wait())

        shutdown.assert_not_called()
        self.assertIn("显式 FARMWATCH_DEVICE，跳过 MuMu 管理", output.getvalue())


class EnvironmentFlagTests(unittest.TestCase):
    def test_env_flag_uses_default_for_unset_and_invalid_values(self):
        self.assertTrue(app._env_flag(None, True))
        self.assertFalse(app._env_flag(None, False))
        self.assertTrue(app._env_flag("maybe", True))
        self.assertFalse(app._env_flag("maybe", False))

    def test_env_flag_accepts_true_values_case_insensitively(self):
        for value in ("1", "true", "TRUE", " yes ", "On"):
            with self.subTest(value=value):
                self.assertTrue(app._env_flag(value, False))

    def test_env_flag_accepts_false_values_case_insensitively(self):
        for value in ("0", "false", "FALSE", " no ", "Off"):
            with self.subTest(value=value):
                self.assertFalse(app._env_flag(value, True))

    def test_resolve_manage_mumu_default_and_overrides(self):
        for platform_is_windows in (True, False):
            for device_from_env in (False, True):
                with self.subTest(
                    platform_is_windows=platform_is_windows,
                    device_from_env=device_from_env,
                ):
                    default = platform_is_windows and not device_from_env
                    self.assertEqual(
                        app._resolve_manage_mumu(
                            device_from_env,
                            None,
                            platform_is_windows=platform_is_windows,
                        ),
                        default,
                    )
                    self.assertTrue(
                        app._resolve_manage_mumu(
                            device_from_env,
                            "1",
                            platform_is_windows=platform_is_windows,
                        )
                    )
                    self.assertFalse(
                        app._resolve_manage_mumu(
                            device_from_env,
                            "0",
                            platform_is_windows=platform_is_windows,
                        )
                    )
                    self.assertEqual(
                        app._resolve_manage_mumu(
                            device_from_env,
                            "invalid",
                            platform_is_windows=platform_is_windows,
                        ),
                        default,
                    )

    def test_resolve_manage_mumu_defaults_to_host_platform_when_unspecified(self):
        self.assertEqual(
            app._resolve_manage_mumu(False, None),
            os.name == "nt",
        )

    def test_wake_ahead_defaults_to_five_minutes(self):
        """3 分钟不足以覆盖模拟器冷启动 + 登录，默认提前量为 5 分钟。"""
        self.assertEqual(app.WAKE_AHEAD_MIN, 5)


class ModuleMumuConfigurationTests(unittest.TestCase):
    def test_blank_device_is_treated_as_unset_and_nonblank_device_is_stripped(self):
        for blank_value in ("", "   ", "\t\n"):
            with self.subTest(device_value=repr(blank_value)):
                resolved = app._resolve_mumu_config(
                    {"FARMWATCH_DEVICE": blank_value},
                    platform_is_windows=True,
                )

                self.assertEqual(resolved, (None, False, True))

        self.assertEqual(
            app._resolve_mumu_config(
                {"FARMWATCH_DEVICE": "  192.0.2.10:5555  "},
                platform_is_windows=True,
            ),
            ("192.0.2.10:5555", True, False),
        )

    def test_blank_device_does_not_change_manage_mumu_overrides(self):
        for configured_value, expected_manage in (("1", True), ("0", False)):
            with self.subTest(configured_value=configured_value):
                resolved = app._resolve_mumu_config(
                    {
                        "FARMWATCH_DEVICE": "   ",
                        "FARMWATCH_MANAGE_MUMU": configured_value,
                    },
                    platform_is_windows=True,
                )

                self.assertEqual(resolved, (None, False, expected_manage))

    def test_module_config_resolution_covers_platform_device_and_overrides(self):
        for platform_is_windows in (True, False):
            for device_value in (None, "192.0.2.10:5555"):
                for configured_value in (None, "1", "0", "invalid"):
                    with self.subTest(
                        platform_is_windows=platform_is_windows,
                        device_value=device_value,
                        configured_value=configured_value,
                    ):
                        environment = {}
                        if device_value is not None:
                            environment["FARMWATCH_DEVICE"] = device_value
                        if configured_value is not None:
                            environment["FARMWATCH_MANAGE_MUMU"] = configured_value

                        (
                            resolved_device,
                            resolved_device_from_env,
                            resolved_manage_mumu,
                        ) = app._resolve_mumu_config(
                            environment,
                            platform_is_windows=platform_is_windows,
                        )

                        self.assertEqual(resolved_device, device_value)
                        self.assertEqual(
                            resolved_device_from_env, device_value is not None
                        )
                        default = platform_is_windows and device_value is None
                        expected_manage = (
                            {"1": True, "0": False}.get(
                                configured_value, default
                            )
                            if configured_value is not None
                            else default
                        )
                        self.assertEqual(resolved_manage_mumu, expected_manage)

    def test_module_globals_use_the_same_environment_resolution(self):
        expected = app._resolve_mumu_config(
            dict(os.environ), platform_is_windows=os.name == "nt"
        )
        self.assertEqual(
            (
                app._device_env_value,
                app._device_from_env,
                app._manage_mumu,
            ),
            expected,
        )


class MainEmulatorGateTests(unittest.TestCase):
    def test_main_checks_emulator_before_adb_and_stops_on_start_failure(self):
        events = []

        def ensure():
            events.append("ensure")
            return False

        def check_adb():
            events.append("adb")
            return False

        with (
            patch.object(app, "ensure_emulator_running", side_effect=ensure),
            patch.object(app, "check_adb_connection", side_effect=check_adb),
            redirect_stdout(StringIO()),
        ):
            app.main()

        self.assertEqual(events, ["ensure"])


class FrozenDateTime(datetime):
    current = datetime(2026, 7, 23, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return tz.fromutc(cls.current.replace(tzinfo=tz))
        return cls.current


class RoundWaitScheduleTests(unittest.TestCase):
    def setUp(self):
        app._pending_target_time = None

    def tearDown(self):
        app._pending_target_time = None

    def test_schedule_uses_configured_wake_ahead_minutes(self):
        maturity_dt = FrozenDateTime.current + timedelta(minutes=30)
        output = StringIO()

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "WAKE_AHEAD_MIN", 3),
            patch.object(app, "adb_shell"),
            redirect_stdout(output),
        ):
            wake_time = app.schedule_next_round(
                (12, 30, 0), None, maturity_dt, is_mature=False
            )

        harvest_target = maturity_dt + timedelta(seconds=60)
        self.assertEqual(wake_time, harvest_target - timedelta(minutes=3))
        self.assertIn("目标时间: 12:31:00", output.getvalue())
        self.assertIn("提前3分钟唤醒: 12:28:00", output.getvalue())

    def test_due_wake_time_skips_shutdown_sleep_and_restart(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "shutdown_emulator_for_wait") as shutdown,
            patch.object(app.time, "sleep") as sleep,
            patch.object(app, "ensure_emulator_running") as ensure,
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current - timedelta(seconds=1)
            )

        self.assertTrue(result)
        shutdown.assert_not_called()
        sleep.assert_not_called()
        ensure.assert_not_called()

    def test_future_wait_continues_after_shutdown_failure_then_restarts(self):
        events = []

        def shutdown():
            events.append("shutdown")
            return False

        def sleep(seconds):
            events.append(("sleep", seconds))

        def ensure():
            events.append("start")
            return True

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(
                app, "shutdown_emulator_for_wait", side_effect=shutdown
            ),
            patch.object(app.time, "sleep", side_effect=sleep),
            patch.object(app, "ensure_emulator_running", side_effect=ensure),
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current + timedelta(minutes=2)
            )

        self.assertTrue(result)
        self.assertEqual(events, ["shutdown", ("sleep", 120.0), "start"])

    def test_future_wait_returns_false_when_restart_fails(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "shutdown_emulator_for_wait") as shutdown,
            patch.object(app.time, "sleep") as sleep,
            patch.object(
                app, "ensure_emulator_running", return_value=False
            ) as ensure,
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current + timedelta(minutes=2)
            )

        self.assertFalse(result)
        shutdown.assert_called_once_with()
        sleep.assert_called_once_with(120.0)
        ensure.assert_called_once_with()

    def test_explicit_device_is_checked_after_managed_restart(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "_device_from_env", True),
            patch.object(app, "shutdown_emulator_for_wait") as shutdown,
            patch.object(app.time, "sleep"),
            patch.object(
                app, "ensure_emulator_running", return_value=True
            ) as ensure,
            patch.object(
                app, "check_adb_connection", return_value=True
            ) as check_adb,
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current + timedelta(minutes=2)
            )

        self.assertTrue(result)
        shutdown.assert_called_once_with()
        ensure.assert_called_once_with()
        check_adb.assert_called_once_with()

    def test_explicit_device_adb_failure_stops_after_managed_restart(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "_device_from_env", True),
            patch.object(app, "shutdown_emulator_for_wait"),
            patch.object(app.time, "sleep"),
            patch.object(
                app, "ensure_emulator_running", return_value=True
            ),
            patch.object(
                app, "check_adb_connection", return_value=False
            ) as check_adb,
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current + timedelta(minutes=2)
            )

        self.assertFalse(result)
        check_adb.assert_called_once_with()

    def test_default_dynamic_device_does_not_repeat_adb_check_after_restart(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "_manage_mumu", True),
            patch.object(app, "_device_from_env", False),
            patch.object(app, "shutdown_emulator_for_wait"),
            patch.object(app.time, "sleep"),
            patch.object(
                app, "ensure_emulator_running", return_value=True
            ),
            patch.object(app, "check_adb_connection") as check_adb,
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current + timedelta(minutes=2)
            )

        self.assertTrue(result)
        check_adb.assert_not_called()

    def test_external_adb_management_disabled_does_not_repeat_adb_check(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "_manage_mumu", False),
            patch.object(app, "_device_from_env", True),
            patch.object(app, "shutdown_emulator_for_wait"),
            patch.object(app.time, "sleep"),
            patch.object(
                app, "ensure_emulator_running", return_value=True
            ),
            patch.object(app, "check_adb_connection") as check_adb,
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                FrozenDateTime.current + timedelta(minutes=2)
            )

        self.assertTrue(result)
        check_adb.assert_not_called()

    def test_unreadable_ocr_retry_is_a_normal_wait(self):
        events = []

        def force_stop(_command):
            events.append("force-stop")

        def shutdown():
            events.append("shutdown")
            return True

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "adb_shell", side_effect=force_stop),
            patch.object(
                app, "shutdown_emulator_for_wait", side_effect=shutdown
            ),
            patch.object(app.time, "sleep") as sleep,
            patch.object(
                app, "ensure_emulator_running", return_value=True
            ) as ensure,
            redirect_stdout(StringIO()),
        ):
            retry_time = app.schedule_next_round(
                None, None, None, is_mature=False
            )
            result = app.wait_for_next_round(retry_time)

        self.assertTrue(result)
        self.assertEqual(retry_time, FrozenDateTime.current + timedelta(minutes=5))
        self.assertEqual(events[:2], ["force-stop", "shutdown"])
        sleep.assert_called_once_with(300.0)
        ensure.assert_called_once_with()

    def test_shutdown_duration_is_subtracted_from_host_wait(self):
        class AdvancingDateTime(FrozenDateTime):
            pass

        AdvancingDateTime.current = FrozenDateTime.current

        def slow_shutdown():
            AdvancingDateTime.current += timedelta(seconds=30)
            return True

        with (
            patch.object(app, "datetime", AdvancingDateTime),
            patch.object(app, "shutdown_emulator_for_wait", side_effect=slow_shutdown),
            patch.object(app.time, "sleep") as sleep,
            patch.object(app, "ensure_emulator_running", return_value=True),
            redirect_stdout(StringIO()),
        ):
            result = app.wait_for_next_round(
                AdvancingDateTime.current + timedelta(seconds=120)
            )

        self.assertTrue(result)
        sleep.assert_called_once_with(90.0)


def main_loop_patches():
    """隔离主循环里每个步骤，只保留被测流程的真实实现。"""
    return (
        patch.object(app, "check_adb_connection", return_value=True),
        patch.object(app, "detect_resolution", return_value=(1280, 720)),
        patch.object(app, "report_device_state"),
        patch.object(app, "launch_game"),
        patch.object(app, "wait_for_game_update", return_value=True),
        patch.object(app, "dismiss_startup_overlays"),
        patch.object(app, "ensure_account_ready", return_value=True),
        patch.object(app, "enter_game", return_value=True),
        patch.object(app, "dismiss_lobby_overlays"),
        patch.object(app, "open_farm", return_value=True),
        patch.object(app, "approach_farm_control"),
        patch.object(app, "perform_farm_action", return_value=True),
        patch.object(app, "collect_harvest_result", return_value=(False, False)),
        patch.object(
            app,
            "inspect_crop",
            return_value=((12, 30, 0), {}, FrozenDateTime.current, False),
        ),
    )


class MainRoundLifecycleTests(unittest.TestCase):
    class StopMain(RuntimeError):
        pass

    def setUp(self):
        app._pending_target_time = None

    def tearDown(self):
        app._pending_target_time = None

    def common_main_patches(self):
        return main_loop_patches()

    def test_main_login_failure_skips_start_game_and_farm_steps(self):
        """A failed login gate retries the round before any game/farm action."""
        patches = self.common_main_patches()
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(
                patch.object(app, "ensure_emulator_running", return_value=True)
            )
            login_check = stack.enter_context(
                patch.object(
                    app,
                    "ensure_account_ready",
                    side_effect=[False, self.StopMain()],
                )
            )
            start_game = stack.enter_context(
                patch.object(
                    app,
                    "enter_game",
                    side_effect=self.StopMain(),
                )
            )
            close_popup = stack.enter_context(
                patch.object(app, "dismiss_lobby_overlays")
            )
            enter_farm = stack.enter_context(
                patch.object(app, "open_farm")
            )
            wait = stack.enter_context(
                patch.object(app, "wait_for_next_round")
            )
            shutdown = stack.enter_context(
                patch.object(app, "shutdown_emulator_for_wait")
            )
            stack.enter_context(redirect_stdout(StringIO()))
            with self.assertRaises(self.StopMain):
                app.main()

        self.assertEqual(login_check.call_count, 2)
        start_game.assert_not_called()
        close_popup.assert_not_called()
        enter_farm.assert_not_called()
        wait.assert_not_called()
        shutdown.assert_not_called()

    def test_main_calls_login_check_before_start_game(self):
        events = []

        def close_startup_popups():
            events.append("startup_overlay")

        def ensure_login_ready():
            events.append("login_check")
            return True

        def click_start_game():
            events.append("start_game")
            raise self.StopMain()

        patches = self.common_main_patches()
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(
                patch.object(app, "ensure_emulator_running", return_value=True)
            )
            stack.enter_context(
                patch.object(
                    app,
                    "dismiss_startup_overlays",
                    side_effect=close_startup_popups,
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "ensure_account_ready",
                    side_effect=ensure_login_ready,
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "enter_game",
                    side_effect=click_start_game,
                )
            )
            stack.enter_context(redirect_stdout(StringIO()))
            with self.assertRaises(self.StopMain):
                app.main()

        self.assertEqual(events, ["startup_overlay", "login_check", "start_game"])

    def test_main_normal_round_orders_start_shutdown_sleep_start_unlock(self):
        events = []
        wake_time = FrozenDateTime.current + timedelta(minutes=2)

        def ensure():
            events.append("start")
            return True

        def unlock():
            events.append("unlock")
            raise self.StopMain()

        patches = self.common_main_patches()
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(patch.object(app, "datetime", FrozenDateTime))
            stack.enter_context(
                patch.object(app, "ensure_emulator_running", side_effect=ensure)
            )
            stack.enter_context(
                patch.object(
                    app,
                    "shutdown_emulator_for_wait",
                    side_effect=lambda: events.append("shutdown") or True,
                )
            )
            stack.enter_context(
                patch.object(
                    app.time,
                    "sleep",
                    side_effect=lambda _seconds: events.append("sleep"),
                )
            )
            stack.enter_context(
                patch.object(app, "schedule_next_round", return_value=wake_time)
            )
            stack.enter_context(patch.object(app, "wake_screen", side_effect=unlock))
            stack.enter_context(redirect_stdout(StringIO()))
            with self.assertRaises(self.StopMain):
                app.main()

        self.assertEqual(events, ["start", "shutdown", "sleep", "start", "unlock"])

    def test_main_returns_without_unlock_when_wait_restart_fails(self):
        patches = self.common_main_patches()
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(
                patch.object(app, "ensure_emulator_running", return_value=True)
            )
            stack.enter_context(
                patch.object(
                    app,
                    "schedule_next_round",
                    return_value=FrozenDateTime.current + timedelta(minutes=2),
                )
            )
            wait = stack.enter_context(
                patch.object(app, "wait_for_next_round", return_value=False)
            )
            unlock = stack.enter_context(patch.object(app, "wake_screen"))
            stack.enter_context(redirect_stdout(StringIO()))
            app.main()

        wait.assert_called_once_with(FrozenDateTime.current + timedelta(minutes=2))
        unlock.assert_not_called()

    def test_mature_immediate_retry_does_not_shutdown_emulator(self):
        with ExitStack() as stack:
            for item in self.common_main_patches():
                stack.enter_context(item)
            # Override the named step without replacing the launch isolation.
            report = stack.enter_context(
                patch.object(
                    app,
                    "report_device_state",
                    side_effect=[None, self.StopMain()],
                )
            )
            stack.enter_context(
                patch.object(
                    app.subprocess,
                    "run",
                    side_effect=AssertionError("Main-loop tests must not run device commands"),
                )
            )
            stack.enter_context(
                patch.object(app, "ensure_emulator_running", return_value=True)
            )
            stack.enter_context(
                patch.object(app, "schedule_next_round", return_value=None)
            )
            shutdown = stack.enter_context(
                patch.object(app, "shutdown_emulator_for_wait")
            )
            wait = stack.enter_context(patch.object(app, "wait_for_next_round"))
            unlock = stack.enter_context(patch.object(app, "wake_screen"))
            stack.enter_context(redirect_stdout(StringIO()))
            with self.assertRaises(self.StopMain):
                app.main()

        self.assertEqual(report.call_count, 2)
        shutdown.assert_not_called()
        wait.assert_not_called()
        unlock.assert_not_called()


class TargetTimeCatchUpTests(unittest.TestCase):
    """执行务农 前补齐提前唤醒量，避免早于浇水/成熟时刻点击一键务农。"""

    def setUp(self):
        app._pending_target_time = None

    def tearDown(self):
        app._pending_target_time = None

    def test_schedule_records_target_time_for_farm_action(self):
        maturity_dt = FrozenDateTime.current + timedelta(minutes=30)

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "WAKE_AHEAD_MIN", 3),
            patch.object(app, "adb_shell"),
            redirect_stdout(StringIO()),
        ):
            wake_time = app.schedule_next_round(
                (12, 30, 0), None, maturity_dt, is_mature=False
            )

        harvest_target = maturity_dt + timedelta(seconds=60)
        self.assertEqual(wake_time, harvest_target - timedelta(minutes=3))
        self.assertEqual(app._pending_target_time, harvest_target)

    def test_schedule_clears_stale_target_when_time_is_unreadable(self):
        app._pending_target_time = FrozenDateTime.current + timedelta(hours=1)

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "adb_shell"),
            redirect_stdout(StringIO()),
        ):
            app.schedule_next_round(None, None, None, is_mature=False)

        self.assertIsNone(app._pending_target_time)

    def test_early_arrival_sleeps_until_target_time(self):
        app._pending_target_time = FrozenDateTime.current + timedelta(seconds=95)
        output = StringIO()

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "WAKE_AHEAD_MIN", 3),
            patch.object(app.time, "sleep") as sleep,
            redirect_stdout(output),
        ):
            waited = app.wait_until_target_time()

        self.assertTrue(waited)
        sleep.assert_called_once_with(95.0)
        self.assertIn("补等 95 秒", output.getvalue())
        self.assertIsNone(app._pending_target_time)

    def test_late_arrival_does_not_sleep(self):
        app._pending_target_time = FrozenDateTime.current - timedelta(seconds=40)

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app.time, "sleep") as sleep,
            redirect_stdout(StringIO()),
        ):
            waited = app.wait_until_target_time()

        self.assertFalse(waited)
        sleep.assert_not_called()
        self.assertIsNone(app._pending_target_time)

    def test_missing_target_does_not_sleep(self):
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app.time, "sleep") as sleep,
            redirect_stdout(StringIO()),
        ):
            waited = app.wait_until_target_time()

        self.assertFalse(waited)
        sleep.assert_not_called()

    def test_wait_is_capped_at_configured_wake_ahead(self):
        app._pending_target_time = FrozenDateTime.current + timedelta(hours=2)

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "WAKE_AHEAD_MIN", 3),
            patch.object(app.time, "sleep") as sleep,
            redirect_stdout(StringIO()),
        ):
            waited = app.wait_until_target_time()

        self.assertTrue(waited)
        sleep.assert_called_once_with(180)

    def test_main_waits_between_statue_move_and_oneclick_farm(self):
        events = []

        with ExitStack() as stack:
            for item in main_loop_patches():
                stack.enter_context(item)
            stack.enter_context(
                patch.object(app, "ensure_emulator_running", return_value=True)
            )
            stack.enter_context(
                patch.object(
                    app,
                    "approach_farm_control",
                    side_effect=lambda: events.append("approach"),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "wait_until_target_time",
                    side_effect=lambda: events.append("catch_up"),
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "perform_farm_action",
                    side_effect=lambda: events.append("farm_action") or True,
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "schedule_next_round",
                    side_effect=MainRoundLifecycleTests.StopMain(),
                )
            )
            stack.enter_context(redirect_stdout(StringIO()))
            with self.assertRaises(MainRoundLifecycleTests.StopMain):
                app.main()

        self.assertEqual(events, ["approach", "catch_up", "farm_action"])


class RunMainExitLifecycleTests(unittest.TestCase):
    def test_keyboard_interrupt_shuts_down_emulator(self):
        with (
            patch.object(app, "main", side_effect=KeyboardInterrupt),
            patch.object(app, "shutdown_emulator_for_wait") as shutdown,
            redirect_stdout(StringIO()),
        ):
            app.run_main()

        shutdown.assert_called_once_with()

    def test_unexpected_error_shuts_down_emulator(self):
        with (
            patch.object(app, "main", side_effect=RuntimeError("boom")),
            patch.object(app, "shutdown_emulator_for_wait") as shutdown,
            redirect_stdout(StringIO()),
        ):
            app.run_main()

        shutdown.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
