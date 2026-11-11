from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np

from tests.support import make_app
from farmwatch.app import FarmApp
from farmwatch.config import ROOT, Settings
from farmwatch.domain.results import RoundStatus


class RefactorBehaviorTests(unittest.TestCase):
    def test_double_failed_action_never_reads_or_schedules_and_retries_are_bounded(self):
        app = make_app()
        with ExitStack() as stack, redirect_stdout(StringIO()):
            for name, value in {'ensure_emulator_running': True, 'check_adb_connection': True,
                                'detect_resolution': (1280, 720), 'launch_game': True,
                                'wait_for_game_update': True, 'ensure_account_ready': True,
                                'enter_game': True, 'open_farm': True,
                                'approach_farm_control': True, 'perform_farm_action': False}.items():
                stack.enter_context(patch.object(app, name, return_value=value))
            for name in ('report_device_state', 'dismiss_startup_overlays', 'dismiss_lobby_overlays',
                         'wait_until_target_time', 'screenshot', '_exit_game_for_wait'):
                stack.enter_context(patch.object(app, name))
            readers = [stack.enter_context(patch.object(app, name)) for name in
                       ('collect_harvest_result', 'inspect_crop', 'schedule_next_round', 'notify_round')]
            sleep = stack.enter_context(patch.object(app.time, 'sleep'))
            with self.assertRaisesRegex(RuntimeError, '连续三轮失败'):
                app.main()
            for reader in readers:
                reader.assert_not_called()
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(app.last_round.status, RoundStatus.ACTION_FAILED)

    def test_failure_exit_code_and_cleanup(self):
        app = make_app()
        with patch.object(app, 'main', side_effect=RuntimeError('test')), \
             patch.object(app, 'shutdown_emulator_for_wait') as close, \
             patch.object(app, 'notify_error_exit'), patch('farmwatch.automation.cycle.logging.getLogger'):
            self.assertEqual(app.run_main(), 1)
        close.assert_called_once()

    def test_valid_other_foreground_has_no_retry_sleep(self):
        app = make_app()
        with patch.object(app, 'adb_shell', return_value='mResumedActivity: com.mumu.launcher/.Main') as adb, \
             patch.object(app.time, 'sleep') as sleep:
            self.assertFalse(app.report_device_state())
        adb.assert_called_once()
        sleep.assert_not_called()

    def test_screenshots_off_keeps_only_working_frame_and_no_login_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = FarmApp(Settings(root=Path(temporary)))
            image = np.zeros((20, 30, 3), dtype='uint8')
            def adb(args, **kwargs):
                if 'pull' in args:
                    cv2.imwrite(args[-1], image)
                return subprocess.CompletedProcess(args, 0, '', '')
            with patch.object(app.subprocess, 'run', side_effect=adb):
                app.screenshot(archive_label='farm_entry')
            self.assertTrue(Path(app.SCREENSHOT_PATH).is_file())
            self.assertFalse(app.SCREENSHOT_ARCHIVE_DIR.exists())
            self.assertIsNone(app._save_login_diagnostic('ERROR'))
            self.assertFalse((Path(temporary) / 'tmp' / 'login_diagnostics').exists())

    def test_key_and_all_archive_policies(self):
        app = FarmApp(Settings(screenshots_enabled=True))
        self.assertFalse(app._archive_enabled_for('capture'))
        for name in ('farm_entry', 'harvest', 'action_fail'):
            self.assertTrue(app._archive_enabled_for(name))
        app.settings = replace(app.settings, screenshots_mode='all')
        self.assertTrue(app._archive_enabled_for('capture'))

    def test_retention_covers_login_images_and_preserves_unknown_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = FarmApp(Settings(root=root, screenshots_retention_hours=.5))
            directory = root / 'tmp' / 'login_diagnostics'
            directory.mkdir(parents=True)
            expired = directory / '20260101_000000_000000-CHECK.png'
            unknown = directory / 'my_photo.png'
            for path in (expired, unknown):
                path.write_bytes(b'fixture')
                os.utime(path, (1, 1))
            app.maintenance(force=True)
            self.assertFalse(expired.exists())
            self.assertTrue(unknown.exists())

    def test_cleanup_is_throttled(self):
        app = make_app()
        with patch.object(app, '_cleanup_screenshot_archive') as cleanup, \
             patch.object(app.time, 'monotonic', side_effect=[0, 5, 599, 600]):
            for _ in range(4):
                app.maintenance(include_login=False)
        self.assertEqual(cleanup.call_count, 2)

    def test_matching_reuses_frames_and_templates_and_rejects_outside_roi(self):
        app = make_app()
        fixture = ROOT / 'tests' / 'fixtures' / 'current' / 'lobby.png'
        with patch.object(cv2, 'imread', wraps=cv2.imread) as read, \
             patch.object(cv2, 'resize', wraps=cv2.resize) as resize, redirect_stdout(StringIO()):
            first = app.locate_control('farm.enter', str(fixture), .55)
            calls = read.call_count, resize.call_count
            second = app.locate_control('farm.enter', str(fixture), .55)
            self.assertEqual(first, second)
            self.assertEqual((read.call_count, resize.call_count), calls)
        self.assertTrue(260 <= first['x'] <= 390 and 520 <= first['y'] <= 565)
        self.assertEqual(resize.call_count, 0)

    def test_instances_do_not_share_pending_target_or_ocr_engine(self):
        one, two = make_app(), make_app()
        one._ocr_engine = object()
        one._pending_target_time = one.datetime.now()
        self.assertIsNone(two._ocr_engine)
        self.assertIsNone(two._pending_target_time)

    def test_real_missing_oneclick_frame_is_not_a_success(self):
        app = make_app()
        fixture = ROOT / 'tests' / 'fixtures' / 'current' / 'farm-idle.png'
        self.assertIsNone(app.locate_farm_action(str(fixture)))

    def test_resolution_uses_override_and_refuses_unknown_size(self):
        app = make_app()
        with patch.object(app, 'adb_shell', return_value='Physical size: 1080x2400\nOverride size: 720x1280'):
            self.assertEqual(app.detect_resolution(), (1280, 720))
        with patch.object(app, 'adb_shell', return_value=''):
            with self.assertRaisesRegex(RuntimeError, '无法读取设备分辨率'):
                app.detect_resolution()
