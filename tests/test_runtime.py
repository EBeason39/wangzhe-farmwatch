from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import farmwatch.runtime.process as process


@unittest.skipUnless(os.name == 'nt', 'Windows process handles')
class RuntimeTests(unittest.TestCase):
    def test_guard_rejects_duplicate_and_stale_creation_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with process.InstanceGuard(root):
                record = process.read_state(root)
                self.assertTrue(process.is_running(root, record))
                with self.assertRaises(process.AlreadyRunning):
                    with process.InstanceGuard(root):
                        self.fail('duplicate worker acquired mutex')
                stale = dict(record, created=record['created'] + 1)
                self.assertIsNone(process.open_verified_process(root, stale))
                process.write_state(root, stale)
                self.assertEqual(process.stop(root, timeout=.1), 0)
                self.assertTrue(process.is_running(root, record))

    def test_corrupt_record_never_selects_a_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process.state_path(root).parent.mkdir()
            process.state_path(root).write_text('broken', encoding='utf-8')
            self.assertFalse(process.is_running(root))
            for pid in (True, -1, '123', 2 ** 80):
                self.assertIsNone(process.open_verified_process(root, {'root': str(root), 'pid': pid}))

    def test_stop_event_and_restart_use_a_real_isolated_worker(self):
        code = """
import sys,time
from pathlib import Path
from farmwatch.runtime.process import InstanceGuard
with InstanceGuard(Path(sys.argv[1])) as guard:
    guard.update('running',manage_mumu=False)
    while not guard.should_stop():
        time.sleep(.02)
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for attempt in range(2):
                child = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-c', code, str(root)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if process.read_state(root).get('phase') == 'running' and process.is_running(root):
                            break
                        if child.poll() is not None:
                            self.fail(child.stderr.read().decode('utf-8', 'replace'))
                        time.sleep(.05)
                    self.assertTrue(process.is_running(root))
                    self.assertEqual(process.stop(root, timeout=3), 0)
                    self.assertEqual(child.wait(timeout=5), 0)
                    self.assertFalse(process.is_running(root))
                    self.assertEqual(process.read_state(root)['phase'], 'stopped')
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
                    child.stdout.close()
                    child.stderr.close()
