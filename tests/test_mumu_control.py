import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import farmwatch.adapters.emulator as mumu_control
FARM_INFO = {
    "0": {
        "index": "0",
        "name": "MuMu安卓设备",
        "is_process_started": False,
        "is_android_started": False,
    },
    "10": {
        "index": "10",
        "name": "farm",
        "is_process_started": True,
        "is_android_started": True,
        "player_state": "start_finished",
        "adb_host_ip": "127.0.0.1",
        "adb_port": 16704,
    },
}


class MumuControlTests(unittest.TestCase):
    def test_find_cli_prefers_environment_path_and_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment_cli = root / "env" / "mumu-cli.exe"
            environment_cli.parent.mkdir()
            environment_cli.touch()
            candidate_cli = root / "candidate" / "mumu-cli.exe"
            candidate_cli.parent.mkdir()
            candidate_cli.touch()

            self.assertEqual(
                mumu_control.find_mumu_cli(
                    env={"FARMWATCH_MUMU_CLI": str(environment_cli)},
                    candidates=[candidate_cli],
                ),
                environment_cli,
            )

            missing_environment_cli = root / "missing" / "mumu-cli.exe"
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.find_mumu_cli(
                    env={"FARMWATCH_MUMU_CLI": str(missing_environment_cli)},
                    candidates=[candidate_cli],
                )
            message = str(raised.exception)
            self.assertIn(str(missing_environment_cli), message)
            self.assertNotIn(str(candidate_cli), message)

    def test_find_cli_uses_first_existing_candidate_and_reports_all_missing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_missing = root / "first" / "mumu-cli.exe"
            existing = root / "second" / "mumu-cli.exe"
            existing.parent.mkdir()
            existing.touch()
            later_existing = root / "third" / "mumu-cli.exe"
            later_existing.parent.mkdir()
            later_existing.touch()

            self.assertEqual(
                mumu_control.find_mumu_cli(
                    env={}, candidates=[first_missing, existing, later_existing]
                ),
                existing,
            )

            final_missing = root / "final" / "mumu-cli.exe"
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.find_mumu_cli(
                    env={}, candidates=[first_missing, final_missing]
                )
            message = str(raised.exception)
            self.assertIn(str(first_missing), message)
            self.assertIn(str(final_missing), message)

    def test_run_cli_uses_argument_list_without_shell_and_returns_stdout(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"ok": true}\n', stderr=""
        )
        cli_path = Path(r"C:\MuMu\mumu-cli.exe")

        with patch.object(mumu_control.subprocess, "run", return_value=completed) as run:
            result = mumu_control.run_mumu_cli(cli_path, "info", "-v", "all", timeout=12)

        self.assertEqual(result, '{"ok": true}\n')
        run.assert_called_once_with(
            [str(cli_path), "info", "-v", "all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=mumu_control._NO_WINDOW_FLAGS,
        )
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_run_cli_converts_process_errors_to_control_errors_with_diagnostics(self):
        cli_path = Path(r"C:\MuMu\mumu-cli.exe")

        with patch.object(
            mumu_control.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=2, stdout="partial output", stderr="bad command"
            ),
        ):
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.run_mumu_cli(cli_path, "info")
            self.assertIn("bad command", str(raised.exception))
            self.assertIn("partial output", str(raised.exception))

        with patch.object(
            mumu_control.subprocess,
            "run",
            side_effect=OSError("permission denied"),
        ):
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.run_mumu_cli(cli_path, "info")
            self.assertIn("permission denied", str(raised.exception))

        timeout = subprocess.TimeoutExpired(
            [str(cli_path), "info"], 30, output="partial", stderr="timed out"
        )
        with patch.object(mumu_control.subprocess, "run", side_effect=timeout):
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.run_mumu_cli(cli_path, "info")
            message = str(raised.exception)
            self.assertIn("timed out", message)
            self.assertIn("partial", message)
            self.assertEqual(message.count("timed out"), 1)
            self.assertEqual(message.count("partial"), 1)

    def test_list_instances_parses_object_dict_values_and_rejects_bad_json(self):
        cli_path = Path(r"C:\MuMu\mumu-cli.exe")
        payload = dict(FARM_INFO)
        payload["metadata"] = "ignored"
        with patch.object(
            mumu_control,
            "run_mumu_cli",
            return_value=json.dumps(payload, ensure_ascii=False),
        ) as run:
            instances = mumu_control.list_mumu_instances(cli_path)

        self.assertEqual(instances, list(FARM_INFO.values()))
        run.assert_called_once_with(
            cli_path, "info", "-v", "all", timeout=30
        )

        with patch.object(
            mumu_control,
            "run_mumu_cli",
            return_value=json.dumps(payload, ensure_ascii=False),
        ) as run:
            mumu_control.list_mumu_instances(cli_path, timeout=7)
        run.assert_called_once_with(
            cli_path, "info", "-v", "all", timeout=7
        )

        malformed = "not json warning text"
        with patch.object(mumu_control, "run_mumu_cli", return_value=malformed):
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.list_mumu_instances(cli_path)
        self.assertIn(malformed, str(raised.exception))

        with patch.object(mumu_control, "run_mumu_cli", return_value="[]"):
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.list_mumu_instances(cli_path)
        self.assertIn("顶层", str(raised.exception))

    def test_find_instance_matches_exact_name_and_lists_available_names(self):
        cli_path = Path(r"C:\MuMu\mumu-cli.exe")
        instances = list(FARM_INFO.values())
        with patch.object(
            mumu_control, "list_mumu_instances", return_value=instances
        ) as list_instances:
            self.assertIs(
                mumu_control.find_mumu_instance(cli_path, "farm"), instances[1]
            )
            list_instances.assert_called_once_with(cli_path, timeout=30)

        with patch.object(mumu_control, "list_mumu_instances", return_value=instances):
            with self.assertRaises(mumu_control.MumuControlError) as raised:
                mumu_control.find_mumu_instance(cli_path, "far")
        message = str(raised.exception)
        self.assertIn("MuMu安卓设备", message)
        self.assertIn("farm", message)

    def test_instance_adb_address_returns_host_port_and_none_when_missing(self):
        self.assertEqual(mumu_control.instance_adb_address(FARM_INFO["10"]), "127.0.0.1:16704")
        self.assertIsNone(mumu_control.instance_adb_address(FARM_INFO["0"]))
        self.assertIsNone(mumu_control.instance_adb_address({"adb_host_ip": "127.0.0.1"}))
        self.assertIsNone(mumu_control.instance_adb_address({"adb_port": 16704}))
        self.assertIsNone(
            mumu_control.instance_adb_address(
                {"adb_host_ip": "", "adb_port": 16704}
            )
        )
        self.assertIsNone(
            mumu_control.instance_adb_address(
                {"adb_host_ip": "   ", "adb_port": 16704}
            )
        )
        self.assertIsNone(
            mumu_control.instance_adb_address(
                {"adb_host_ip": "127.0.0.1", "adb_port": None}
            )
        )
        self.assertIsNone(
            mumu_control.instance_adb_address(
                {"adb_host_ip": "127.0.0.1", "adb_port": ""}
            )
        )


class MumuLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.cli = Path("mumu-cli.exe")
        self.adb = Path("adb.exe")
        self.stopped = {
            "index": "10",
            "name": "farm",
            "is_process_started": False,
            "is_android_started": False,
        }
        self.started = {
            **self.stopped,
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 16704,
        }

    def test_adb_is_ready_uses_argument_list_and_handles_failures(self):
        device = "127.0.0.1:16704"
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="device\n", stderr=""
        )
        with patch.object(
            mumu_control.subprocess, "run", return_value=completed
        ) as run:
            self.assertTrue(mumu_control.adb_is_ready(self.adb, device))

        run.assert_called_once_with(
            [str(self.adb), "-s", device, "get-state"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=mumu_control._NO_WINDOW_FLAGS,
        )

        with patch.object(
            mumu_control.subprocess, "run", return_value=completed
        ) as run:
            self.assertTrue(
                mumu_control.adb_is_ready(self.adb, device, timeout=1.5)
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 1.5)

        unsuccessful_results = (
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout="device\n", stderr="failed"
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="offline\n", stderr=""
            ),
        )
        for result in unsuccessful_results:
            with self.subTest(result=result), patch.object(
                mumu_control.subprocess, "run", return_value=result
            ):
                self.assertFalse(mumu_control.adb_is_ready(self.adb, device))

    def test_adb_connect_uses_argument_list_and_handles_failures(self):
        device = "127.0.0.1:16704"
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="connected to 127.0.0.1:16704\n", stderr=""
        )
        with patch.object(
            mumu_control.subprocess, "run", return_value=completed
        ) as run:
            self.assertTrue(mumu_control.adb_connect(self.adb, device))

        run.assert_called_once_with(
            [str(self.adb), "connect", device],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=mumu_control._NO_WINDOW_FLAGS,
        )

        with patch.object(
            mumu_control.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="failed"
            ),
        ):
            self.assertFalse(mumu_control.adb_connect(self.adb, device))

        process_errors = (
            OSError("adb unavailable"),
            subprocess.TimeoutExpired([str(self.adb), "connect", device], 10),
        )
        for error in process_errors:
            with self.subTest(error=type(error).__name__), patch.object(
                mumu_control.subprocess, "run", side_effect=error
            ):
                self.assertFalse(mumu_control.adb_connect(self.adb, device))

        process_errors = (
            OSError("adb unavailable"),
            subprocess.TimeoutExpired([str(self.adb), "get-state"], 10),
        )
        for error in process_errors:
            with self.subTest(error=type(error).__name__), patch.object(
                mumu_control.subprocess, "run", side_effect=error
            ):
                self.assertFalse(mumu_control.adb_is_ready(self.adb, device))

    def test_ensure_running_reuses_ready_instance_without_launch(self):
        with (
            patch.object(
                mumu_control, "find_mumu_instance", return_value=self.started
            ) as find_instance,
            patch.object(mumu_control, "adb_is_ready", return_value=True) as adb_ready,
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                sleep_func=Mock(),
                monotonic_func=Mock(return_value=0),
            )

        self.assertEqual(device, "127.0.0.1:16704")
        find_instance.assert_called_once_with(self.cli, "farm", timeout=30)
        adb_ready.assert_called_once_with(
            self.adb, "127.0.0.1:16704", timeout=10
        )
        run_cli.assert_not_called()

    def test_ensure_running_connects_dynamic_address_after_initial_adb_not_ready(self):
        sleep = Mock()
        with (
            patch.object(
                mumu_control, "find_mumu_instance", return_value=self.started
            ) as find_instance,
            patch.object(
                mumu_control, "adb_is_ready", side_effect=[False, True]
            ) as adb_ready,
            patch.object(mumu_control, "adb_connect", return_value=True) as adb_connect,
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                sleep_func=sleep,
                monotonic_func=Mock(return_value=0),
            )

        self.assertEqual(device, "127.0.0.1:16704")
        adb_ready.assert_has_calls(
            [
                call(self.adb, "127.0.0.1:16704", timeout=10),
                call(self.adb, "127.0.0.1:16704", timeout=10),
            ]
        )
        adb_connect.assert_called_once_with(
            self.adb, "127.0.0.1:16704", timeout=10
        )
        find_instance.assert_called_once_with(self.cli, "farm", timeout=30)
        run_cli.assert_not_called()
        sleep.assert_not_called()

    def test_ensure_running_launch_path_connects_before_second_get_state(self):
        ready = {
            **self.started,
            "adb_port": 17777,
        }
        events = []

        def get_state(*_args, **_kwargs):
            events.append("get-state")
            return events.count("get-state") == 2

        def connect(*_args, **_kwargs):
            events.append("connect")
            return True

        sleep = Mock()
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, ready],
            ) as find_instance,
            patch.object(mumu_control, "adb_is_ready", side_effect=get_state) as adb_ready,
            patch.object(mumu_control, "adb_connect", side_effect=connect) as adb_connect,
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                sleep_func=sleep,
                monotonic_func=Mock(return_value=0),
            )

        self.assertEqual(device, "127.0.0.1:17777")
        self.assertEqual(events, ["get-state", "connect", "get-state"])
        self.assertEqual(
            find_instance.call_args_list,
            [
                call(self.cli, "farm", timeout=30),
                call(self.cli, "farm", timeout=30),
            ],
        )
        run_cli.assert_called_once_with(
            self.cli, "control", "-v", "10", "launch", timeout=30
        )
        adb_ready.assert_has_calls(
            [
                call(self.adb, "127.0.0.1:17777", timeout=10),
                call(self.adb, "127.0.0.1:17777", timeout=10),
            ]
        )
        adb_connect.assert_called_once_with(
            self.adb, "127.0.0.1:17777", timeout=10
        )
        sleep.assert_not_called()

    def test_adb_ready_helper_uses_decreasing_remaining_deadline(self):
        device = "127.0.0.1:16704"
        monotonic = Mock(side_effect=[105, 112, 117])
        with (
            patch.object(
                mumu_control, "adb_is_ready", side_effect=[False, True]
            ) as adb_ready,
            patch.object(mumu_control, "adb_connect", return_value=True) as adb_connect,
        ):
            ready, attempted = mumu_control._adb_ready_with_optional_connect(
                self.adb,
                device,
                deadline=120,
                timeout_message="deadline expired",
                connect_attempted=False,
                monotonic_func=monotonic,
            )

        self.assertTrue(ready)
        self.assertTrue(attempted)
        self.assertEqual(
            adb_ready.call_args_list,
            [
                call(self.adb, device, timeout=10),
                call(self.adb, device, timeout=3),
            ],
        )
        adb_connect.assert_called_once_with(self.adb, device, timeout=8)

    def test_adb_ready_helper_stops_when_deadline_expires_after_connect(self):
        device = "127.0.0.1:16704"
        monotonic = Mock(side_effect=[102, 103, 110])
        with (
            patch.object(mumu_control, "adb_is_ready", return_value=False) as adb_ready,
            patch.object(mumu_control, "adb_connect", return_value=True) as adb_connect,
            self.assertRaisesRegex(mumu_control.MumuControlError, "deadline expired"),
        ):
            mumu_control._adb_ready_with_optional_connect(
                self.adb,
                device,
                deadline=110,
                timeout_message="deadline expired",
                connect_attempted=False,
                monotonic_func=monotonic,
            )

        adb_ready.assert_called_once_with(self.adb, device, timeout=8)
        adb_connect.assert_called_once_with(self.adb, device, timeout=7)

    def test_ensure_running_launches_and_returns_dynamic_ready_address(self):
        ready = {
            **self.started,
            "adb_port": 17777,
        }
        sleep = Mock()
        monotonic = Mock(return_value=0)
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, ready],
            ),
            patch.object(mumu_control, "adb_is_ready", return_value=True) as adb_ready,
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                sleep_func=sleep,
                monotonic_func=monotonic,
            )

        self.assertEqual(device, "127.0.0.1:17777")
        run_cli.assert_called_once_with(
            self.cli, "control", "-v", "10", "launch", timeout=30
        )
        adb_ready.assert_called_once_with(
            self.adb, "127.0.0.1:17777", timeout=10
        )

    def test_launch_command_failure_retries_three_times(self):
        sleep = Mock()
        with (
            patch.object(
                mumu_control, "find_mumu_instance", return_value=self.stopped
            ),
            patch.object(
                mumu_control,
                "run_mumu_cli",
                side_effect=mumu_control.MumuControlError("launch failed"),
            ) as run_cli,
            self.assertRaisesRegex(mumu_control.MumuControlError, "3 次"),
        ):
            mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                sleep_func=sleep,
                monotonic_func=Mock(return_value=0),
            )

        self.assertEqual(run_cli.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(2)

    def test_ensure_running_timeout_reports_seconds(self):
        starting = {
            **self.stopped,
            "is_process_started": True,
            "is_android_started": False,
        }
        sleep = Mock()
        monotonic = Mock(side_effect=[0, 0, 0, 0, 0, 181])
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, starting],
            ),
            patch.object(mumu_control, "adb_is_ready") as adb_ready,
            patch.object(mumu_control, "run_mumu_cli"),
            self.assertRaisesRegex(mumu_control.MumuControlError, "180"),
        ):
            mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                timeout=180,
                sleep_func=sleep,
                monotonic_func=monotonic,
            )

        sleep.assert_called_once_with(2)
        adb_ready.assert_not_called()

    def test_shutdown_returns_when_instance_is_already_stopped(self):
        with (
            patch.object(
                mumu_control, "find_mumu_instance", return_value=self.stopped
            ),
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            stopped = mumu_control.shutdown_mumu(
                self.cli,
                "farm",
                sleep_func=Mock(),
                monotonic_func=Mock(return_value=0),
            )

        self.assertTrue(stopped)
        run_cli.assert_not_called()

    def test_shutdown_sends_command_and_polls_until_stopped(self):
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.started, self.stopped],
            ),
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            stopped = mumu_control.shutdown_mumu(
                self.cli,
                "farm",
                sleep_func=Mock(),
                monotonic_func=Mock(return_value=0),
            )

        self.assertTrue(stopped)
        run_cli.assert_called_once_with(
            self.cli, "control", "-v", "10", "shutdown", timeout=30
        )

    def test_shutdown_timeout_reports_seconds(self):
        sleep = Mock()
        monotonic = Mock(side_effect=[0, 0, 0, 0, 61])
        with (
            patch.object(
                mumu_control, "find_mumu_instance", return_value=self.started
            ),
            patch.object(mumu_control, "run_mumu_cli"),
            self.assertRaisesRegex(mumu_control.MumuControlError, "60"),
        ):
            mumu_control.shutdown_mumu(
                self.cli,
                "farm",
                timeout=60,
                sleep_func=sleep,
                monotonic_func=monotonic,
            )

        sleep.assert_not_called()

    def test_ensure_running_total_deadline_bounds_external_operations(self):
        ready = {
            **self.started,
            "adb_port": 17777,
        }
        monotonic = Mock(side_effect=[100, 105, 112, 113, 118])
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, ready],
            ) as find_instance,
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
            patch.object(mumu_control, "adb_is_ready", return_value=True) as adb_ready,
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                timeout=20,
                sleep_func=Mock(),
                monotonic_func=monotonic,
            )

        self.assertEqual(device, "127.0.0.1:17777")
        self.assertEqual(
            find_instance.call_args_list,
            [
                call(self.cli, "farm", timeout=15),
                call(self.cli, "farm", timeout=7),
            ],
        )
        run_cli.assert_called_once_with(
            self.cli, "control", "-v", "10", "launch", timeout=8
        )
        adb_ready.assert_called_once_with(
            self.adb, "127.0.0.1:17777", timeout=2
        )

    def test_ensure_running_timeout_zero_skips_all_blocking_operations(self):
        find_instance = Mock()
        run_cli = Mock()
        adb_ready = Mock()
        with (
            patch.object(mumu_control, "find_mumu_instance", find_instance),
            patch.object(mumu_control, "run_mumu_cli", run_cli),
            patch.object(mumu_control, "adb_is_ready", adb_ready),
            self.assertRaisesRegex(mumu_control.MumuControlError, "0"),
        ):
            mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                timeout=0,
                sleep_func=Mock(),
                monotonic_func=Mock(side_effect=[5, 5]),
            )

        find_instance.assert_not_called()
        run_cli.assert_not_called()
        adb_ready.assert_not_called()

    def test_ensure_running_sleep_does_not_exceed_remaining_budget(self):
        starting = {
            **self.stopped,
            "is_process_started": True,
            "is_android_started": False,
        }
        sleep = Mock()
        monotonic = Mock(side_effect=[0, 0, 0, 0, 9, 10])
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, starting],
            ) as find_instance,
            patch.object(mumu_control, "run_mumu_cli"),
            patch.object(mumu_control, "adb_is_ready") as adb_ready,
            self.assertRaisesRegex(mumu_control.MumuControlError, "10"),
        ):
            mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                timeout=10,
                poll_interval=2,
                sleep_func=sleep,
                monotonic_func=monotonic,
            )

        sleep.assert_called_once_with(1)
        self.assertEqual(find_instance.call_count, 2)
        adb_ready.assert_not_called()

    def test_launch_error_rechecks_state_before_retrying(self):
        starting = {
            **self.stopped,
            "is_process_started": True,
            "is_android_started": False,
        }
        ready = {
            **self.started,
            "adb_port": 17777,
        }
        sleep = Mock()
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, starting, ready],
            ),
            patch.object(
                mumu_control,
                "run_mumu_cli",
                side_effect=[mumu_control.MumuControlError("launch uncertain")],
            ) as run_cli,
            patch.object(mumu_control, "adb_is_ready", return_value=True),
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                sleep_func=sleep,
                monotonic_func=Mock(return_value=0),
            )

        self.assertEqual(device, "127.0.0.1:17777")
        self.assertEqual(run_cli.call_count, 1)
        sleep.assert_not_called()

    def test_ensure_running_restarts_stale_process_after_delay(self):
        starting = {
            **self.stopped,
            "is_process_started": True,
            "is_android_started": False,
        }
        ready = {
            **self.started,
            "adb_port": 17777,
        }
        clock = {"now": 0}

        def monotonic():
            clock["now"] += 10
            return clock["now"]

        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.stopped, starting, starting, starting, self.stopped, ready, ready],
            ) as find_instance,
            patch.object(
                mumu_control,
                "run_mumu_cli",
                side_effect=[None, None, None],
            ) as run_cli,
            patch.object(
                mumu_control, "adb_is_ready", side_effect=[False, False, True]
            ) as adb_ready,
            patch.object(mumu_control, "adb_connect", return_value=True) as adb_connect,
        ):
            device = mumu_control.ensure_mumu_running(
                self.cli,
                "farm",
                self.adb,
                timeout=300,
                stale_restart_delay=10,
                sleep_func=Mock(),
                monotonic_func=monotonic,
            )

        self.assertEqual(device, "127.0.0.1:17777")
        self.assertEqual(
            [call_item.args[-1] for call_item in run_cli.call_args_list],
            ["launch", "shutdown", "launch"],
        )
        self.assertEqual(find_instance.call_count, 7)
        adb_connect.assert_called_once_with(
            self.adb, "127.0.0.1:17777", timeout=10
        )
    def test_ensure_running_rejects_incomplete_state_and_missing_index(self):
        invalid_states = (
            {key: value for key, value in self.stopped.items()
             if key != "is_process_started"},
            {**self.stopped, "is_process_started": None},
        )
        for instance in invalid_states:
            with (
                self.subTest(kind="state", instance=instance),
                patch.object(
                    mumu_control, "find_mumu_instance", return_value=instance
                ),
                patch.object(
                    mumu_control,
                    "run_mumu_cli",
                    side_effect=AssertionError("launch must not be sent"),
                ) as run_cli,
                patch.object(mumu_control, "adb_is_ready") as adb_ready,
                self.assertRaisesRegex(
                    mumu_control.MumuControlError, "is_process_started"
                ),
            ):
                mumu_control.ensure_mumu_running(
                    self.cli,
                    "farm",
                    self.adb,
                    timeout=10,
                    sleep_func=Mock(),
                    monotonic_func=Mock(return_value=0),
                )
            run_cli.assert_not_called()
            adb_ready.assert_not_called()

        invalid_indexes = (
            {key: value for key, value in self.stopped.items() if key != "index"},
            {**self.stopped, "index": None},
            {**self.stopped, "index": ""},
            {**self.stopped, "index": "   "},
        )
        for instance in invalid_indexes:
            with (
                self.subTest(kind="index", instance=instance),
                patch.object(
                    mumu_control, "find_mumu_instance", return_value=instance
                ),
                patch.object(
                    mumu_control,
                    "run_mumu_cli",
                    side_effect=AssertionError("launch must not be sent"),
                ) as run_cli,
                self.assertRaisesRegex(mumu_control.MumuControlError, "index"),
            ):
                mumu_control.ensure_mumu_running(
                    self.cli,
                    "farm",
                    self.adb,
                    timeout=10,
                    sleep_func=Mock(),
                    monotonic_func=Mock(return_value=0),
                )
            run_cli.assert_not_called()

    def test_shutdown_total_deadline_bounds_external_operations(self):
        monotonic = Mock(side_effect=[100, 105, 112, 118])
        with (
            patch.object(
                mumu_control,
                "find_mumu_instance",
                side_effect=[self.started, self.stopped],
            ) as find_instance,
            patch.object(mumu_control, "run_mumu_cli") as run_cli,
        ):
            stopped = mumu_control.shutdown_mumu(
                self.cli,
                "farm",
                timeout=20,
                sleep_func=Mock(),
                monotonic_func=monotonic,
            )

        self.assertTrue(stopped)
        self.assertEqual(
            find_instance.call_args_list,
            [
                call(self.cli, "farm", timeout=15),
                call(self.cli, "farm", timeout=2),
            ],
        )
        run_cli.assert_called_once_with(
            self.cli, "control", "-v", "10", "shutdown", timeout=8
        )

    def test_shutdown_timeout_zero_skips_all_blocking_operations(self):
        find_instance = Mock()
        run_cli = Mock()
        with (
            patch.object(mumu_control, "find_mumu_instance", find_instance),
            patch.object(mumu_control, "run_mumu_cli", run_cli),
            self.assertRaisesRegex(mumu_control.MumuControlError, "0"),
        ):
            mumu_control.shutdown_mumu(
                self.cli,
                "farm",
                timeout=0,
                sleep_func=Mock(),
                monotonic_func=Mock(side_effect=[5, 5]),
            )

        find_instance.assert_not_called()
        run_cli.assert_not_called()

    def test_shutdown_sleep_does_not_exceed_remaining_budget(self):
        sleep = Mock()
        monotonic = Mock(side_effect=[0, 0, 0, 0, 9, 10])
        with (
            patch.object(
                mumu_control, "find_mumu_instance", return_value=self.started
            ) as find_instance,
            patch.object(mumu_control, "run_mumu_cli"),
            self.assertRaisesRegex(mumu_control.MumuControlError, "10"),
        ):
            mumu_control.shutdown_mumu(
                self.cli,
                "farm",
                timeout=10,
                poll_interval=2,
                sleep_func=sleep,
                monotonic_func=monotonic,
            )

        sleep.assert_called_once_with(1)
        self.assertEqual(find_instance.call_count, 2)

    def test_shutdown_rejects_incomplete_state_and_missing_index(self):
        invalid_states = (
            {key: value for key, value in self.started.items()
             if key != "is_process_started"},
            {**self.started, "is_process_started": None},
        )
        for instance in invalid_states:
            with (
                self.subTest(kind="state", instance=instance),
                patch.object(
                    mumu_control, "find_mumu_instance", return_value=instance
                ),
                patch.object(mumu_control, "run_mumu_cli") as run_cli,
                self.assertRaisesRegex(
                    mumu_control.MumuControlError, "is_process_started"
                ),
            ):
                mumu_control.shutdown_mumu(
                    self.cli,
                    "farm",
                    timeout=10,
                    sleep_func=Mock(),
                    monotonic_func=Mock(return_value=0),
                )
            run_cli.assert_not_called()

        invalid_indexes = (
            {key: value for key, value in self.started.items() if key != "index"},
            {**self.started, "index": None},
            {**self.started, "index": ""},
            {**self.started, "index": "   "},
        )
        for instance in invalid_indexes:
            with (
                self.subTest(kind="index", instance=instance),
                patch.object(
                    mumu_control, "find_mumu_instance", return_value=instance
                ),
                patch.object(
                    mumu_control,
                    "run_mumu_cli",
                    side_effect=AssertionError("shutdown must not be sent"),
                ) as run_cli,
                self.assertRaisesRegex(mumu_control.MumuControlError, "index"),
            ):
                mumu_control.shutdown_mumu(
                    self.cli,
                    "farm",
                    timeout=10,
                    sleep_func=Mock(),
                    monotonic_func=Mock(return_value=0),
                )
            run_cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
