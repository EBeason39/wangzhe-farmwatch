from contextlib import redirect_stdout
from dataclasses import replace
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from farmwatch.config import Settings
from farmwatch.integrations.push import Notifier
from farmwatch.runtime.logging import SecretFormatter
import logging


class NotificationChannelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(root=Path(self.temporary.name), notify_spt='private-spt',
                                 notify_sendkey='private-key', notify_daily_limit=1)

    def test_wxpusher_payload_and_daily_limit(self):
        notifier = Notifier(self.settings)
        with patch('farmwatch.integrations.push._open_url_with_direct_fallback', return_value=BytesIO(b'{"code":1000}')) as request:
            self.assertTrue(notifier.send_push('title', 'body'))
            self.assertFalse(notifier.send_push('again', 'body'))
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['spt'], 'private-spt')
        self.assertEqual(payload['summary'], 'title')
        request.assert_called_once()

    def test_serverchan_payload_has_no_implicit_channel_fallback(self):
        notifier = Notifier(replace(self.settings, notify_channel='serverchan'))
        with patch('farmwatch.integrations.push._open_url_with_direct_fallback', return_value=BytesIO(b'{"code":0}')) as request:
            self.assertTrue(notifier.send_push('title', 'body'))
        req = request.call_args.args[0]
        self.assertEqual(parse_qs(req.data.decode())['desp'], ['body'])
        self.assertIn('private-key', req.full_url)
        request.assert_called_once()

    def test_disabled_or_missing_key_sends_nothing_and_uses_no_quota(self):
        for config in (replace(self.settings, notify_enabled=False), replace(self.settings, notify_spt='')):
            notifier = Notifier(config)
            with patch('farmwatch.integrations.push._open_url_with_direct_fallback') as request:
                self.assertFalse(notifier.send_push('test'))
            request.assert_not_called()
            self.assertFalse(notifier.state_path.exists())

    def test_failed_response_does_not_leak_secrets(self):
        output = StringIO()
        with patch('farmwatch.integrations.push._open_url_with_direct_fallback', side_effect=OSError('private-key')), redirect_stdout(output):
            self.assertFalse(Notifier(self.settings).send_push('test'))
        self.assertNotIn('private-key', output.getvalue())

    def test_formatter_redacts_tracebacks_too(self):
        formatter = SecretFormatter(['private-spt'])
        try:
            raise ValueError('private-spt')
        except ValueError:
            import sys
            record = logging.LogRecord('farmwatch', logging.ERROR, '', 1, 'private-spt', (), sys.exc_info())
        self.assertNotIn('private-spt', formatter.format(record))
