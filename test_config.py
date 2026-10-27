import json
from pathlib import Path
import tempfile
import unittest
import yaml
from generate import generate
from registration import change
import subprocess


class ConfigurationSafety(unittest.TestCase):
    def test_registration_survives_reinstall_and_can_close(self):
        with tempfile.TemporaryDirectory() as folder:
            generate(folder, 'example.com', '203.0.113.10')
            root = Path(folder)
            original = json.loads((root / 'state.json').read_text())
            change(root, 'open', run=lambda *a, **k: None)
            generate(folder, 'example.com', '203.0.113.10')
            config = yaml.safe_load((root / 'synapse/homeserver.yaml').read_text())
            self.assertTrue(config['enable_registration'])
            self.assertTrue(config['enable_registration_without_verification'])
            self.assertEqual(original['registration_secret'], config['registration_shared_secret'])
            change(root, 'close', run=lambda *a, **k: None)
            self.assertFalse(json.loads((root / 'state.json').read_text())['registration_open'])

    def test_failed_registration_validation_restores_original_files(self):
        with tempfile.TemporaryDirectory() as folder:
            generate(folder, 'example.com', '203.0.113.10')
            root = Path(folder)
            paths = [root / 'state.json', root / 'synapse/homeserver.yaml']
            original = [p.read_bytes() for p in paths]
            def fail(*args, **kwargs):
                raise subprocess.CalledProcessError(1, 'validation')
            with self.assertRaises(subprocess.CalledProcessError):
                change(root, 'open', run=fail)
            self.assertEqual(original, [p.read_bytes() for p in paths])

    def test_repeat_keeps_credentials_and_rejects_identity_change(self):
        with tempfile.TemporaryDirectory() as folder:
            generate(folder, 'example.com', '203.0.113.10')
            original = (Path(folder) / 'state.json').read_bytes()
            generate(folder, 'example.com', '203.0.113.10')
            self.assertEqual(original, (Path(folder) / 'state.json').read_bytes())
            with self.assertRaises(ValueError):
                generate(folder, 'different.example', '203.0.113.10')
            self.assertEqual(original, (Path(folder) / 'state.json').read_bytes())

    def test_generated_stack_security_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            generate(folder, 'example.com', '203.0.113.10')
            root = Path(folder)
            services = yaml.safe_load((root / 'compose.yaml').read_text())['services']
            self.assertNotIn('ports', services['postgres'])
            for service in services.values():
                for port in service.get('ports', []):
                    self.assertTrue(port.startswith('127.0.0.1:'), port)
            synapse = yaml.safe_load((root / 'synapse/homeserver.yaml').read_text())
            self.assertFalse(synapse['enable_registration'])
            livekit = yaml.safe_load((root / 'livekit.yaml').read_text())
            self.assertFalse(livekit['room']['auto_create'])
            self.assertEqual(livekit['bind_addresses'], ['127.0.0.1'])
            state = json.loads((root / 'state.json').read_text())
            self.assertEqual(livekit['keys'][state['livekit_key']], services['jwt']['environment']['LIVEKIT_SECRET'])
            self.assertEqual(services['jwt']['environment']['LIVEKIT_FULL_ACCESS_HOMESERVERS'], synapse['server_name'])
            self.assertTrue(livekit['turn']['proxy_protocol'])

    def test_rejects_config_injection_before_writing(self):
        for domain in ['example.com;id', 'example.com\nserver {}', '../example.com', 'https://example.com']:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    generate(folder, domain, '203.0.113.10')
                self.assertFalse((Path(folder) / 'state.json').exists())


if __name__ == '__main__':
    unittest.main()
