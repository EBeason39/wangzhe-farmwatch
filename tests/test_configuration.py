import configparser
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from farmwatch.config import ConfigError, ROOT, Settings, initialize_settings, load_settings


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'settings.ini'

    def write(self, text):
        self.file.write_text(text, encoding='utf-8')
        return load_settings(root=self.root, environment={})

    def test_defaults_disable_archives_and_keep_friends_and_channels(self):
        settings = load_settings(root=self.root, environment={})
        self.assertFalse(settings.screenshots_enabled)
        self.assertTrue(settings.friends_enabled)
        self.assertEqual(settings.notify_channel, 'wxpusher')
        self.assertEqual(settings.screenshots_retention_hours, 72)

    def test_environment_overrides_file_and_external_device_disables_management(self):
        self.write('[mumu]\nmode=auto\ninstance=file\n[farm]\nwake_ahead_min=9\n')
        settings = load_settings(root=self.root, environment={'FARMWATCH_DEVICE': '127.0.0.1:16000',
                                 'FARMWATCH_MUMU_NAME': 'env', 'FARMWATCH_WAKE_AHEAD_MIN': '2'})
        self.assertFalse(settings.manage_mumu)
        self.assertEqual(settings.instance, 'env')
        self.assertEqual(settings.wake_ahead_min, 2)

    def test_explicit_management_override_and_legacy_port(self):
        self.write('[mumu]\nmode=manual\nport=16000\n')
        settings = load_settings(root=self.root, environment={'FARMWATCH_MANAGE_MUMU': 'true'})
        self.assertTrue(settings.manage_mumu)
        self.assertEqual(settings.device, '127.0.0.1:16000')

    def test_comment_template_loads_without_credentials(self):
        settings = load_settings(ROOT / 'settings.example.ini', environment={}, root=ROOT)
        self.assertFalse(settings.notify_spt)
        self.assertFalse(settings.notify_sendkey)

    def test_fractional_retention_and_both_channels(self):
        settings = self.write('[screenshots]\nenabled=true\nmode=all\nretention_hours=.5\n[notify]\nchannel=serverchan\nsendkey=secret%key\n')
        self.assertEqual(settings.screenshots_retention_hours, .5)
        self.assertEqual(settings.notify_sendkey, 'secret%key')
        self.assertNotIn('secret%key', repr(settings))

    def test_invalid_values_are_rejected(self):
        for content in ('[friends]\nenabled=perhaps', '[screenshots]\nretention_hours=nan',
                        '[screenshots]\nretention_hours=0', '[screenshots]\nmode=everything',
                        '[farm]\nwake_ahead_min=-1', '[logging]\nretention_days=0',
                        '[mumu]\nmode=manual', '[notify]\nchannel=typo', '[friends]\nenabeld=true'):
            with self.subTest(content=content), self.assertRaises(ConfigError):
                self.write(content)

    def test_initialize_preserves_existing_private_configuration(self):
        self.file.write_text('[notify]\nchannel=serverchan\nspt=already-set\nsendkey=\n', encoding='utf-8')
        before = self.file.read_bytes()
        initialize_settings(self.root)
        settings = load_settings(root=self.root, environment={})
        self.assertEqual(settings.notify_channel, 'serverchan')
        self.assertEqual(settings.notify_spt, 'already-set')
        self.assertEqual(self.file.read_bytes(), before)

    def test_new_config_uses_documented_template_without_credentials(self):
        (self.root / 'settings.example.ini').write_bytes((ROOT / 'settings.example.ini').read_bytes())
        initialize_settings(self.root)
        self.assertIn('; 王者农场助手', self.file.read_text(encoding='utf-8'))
        settings = load_settings(root=self.root, environment={})
        self.assertEqual(settings.notify_channel, 'wxpusher')
        self.assertFalse(settings.notify_spt or settings.notify_sendkey)
