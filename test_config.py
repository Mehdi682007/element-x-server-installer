import json
from pathlib import Path
import tempfile
import unittest
import yaml
from generate import generate


class ConfigurationSafety(unittest.TestCase):
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
