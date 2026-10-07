"""Test installation against temporary fake upstream trees, never the live app."""
import json
from pathlib import Path
import tempfile
import unittest

import install
from patch_heretic import old, new, OLD_CALL, NEW_CALL


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        for name in ('wgp.py', 'env_venv/Scripts/python.exe'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', encoding='utf-8')
        self.encoder = self.root / 'models/minimax_h3/minimax_h3_main.py'
        self.encoder.parent.mkdir(parents=True)
        self.encoder.write_text(old + '\n\ndef load():\n    offload.load_model_data(model, ' + OLD_CALL + '\n', encoding='utf-8')
        (self.root / 'wgp_config.json').write_text(json.dumps({'custom_option': 123}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_install_backup_idempotence_and_no_personal_data_copy(self):
        job = self.root / 'studio/jobs/example'
        job.mkdir(parents=True)
        (job / 'untouched.txt').write_text('personal fixture', encoding='utf-8')
        original = self.encoder.read_bytes()
        backup = install.install(self.root)
        self.assertEqual((backup / 'models/minimax_h3/minimax_h3_main.py').read_bytes(), original)
        source = self.encoder.read_text(encoding='utf-8')
        self.assertIn(new, source)
        self.assertIn(NEW_CALL, source)
        config = install.read_json(self.root / 'wgp_config.json')
        self.assertEqual(config['custom_option'], 123)
        self.assertEqual(config['keep_intermediate_sliding_windows'], 0)
        self.assertEqual(config['enhancer_enabled'], 0)
        self.assertEqual(config['enabled_plugins'], [])
        self.assertEqual(install.read_json(self.root / 'finetunes/h3_local_heretic.json')['prompt'], '')
        self.assertEqual((job / 'untouched.txt').read_text(), 'personal fixture')
        install.install(self.root)
        self.assertEqual(self.encoder.read_text(encoding='utf-8'), source)

    def test_unknown_upstream_fails_before_mutation(self):
        self.encoder.write_text('unknown upstream version', encoding='utf-8')
        config = (self.root / 'wgp_config.json').read_bytes()
        with self.assertRaises(ValueError):
            install.install(self.root)
        self.assertFalse((self.root / 'Start-H3.ps1').exists())
        self.assertEqual((self.root / 'wgp_config.json').read_bytes(), config)

    def test_check_is_read_only(self):
        before = set(self.root.rglob('*'))
        install.install(self.root, check=True)
        self.assertEqual(before, set(self.root.rglob('*')))

    def test_running_server_lock_prevents_install(self):
        with install.server_lock(self.root):
            with self.assertRaises(OSError):
                install.install(self.root)
        self.assertFalse((self.root / 'Start-H3.ps1').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
