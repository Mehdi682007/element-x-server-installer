import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from detect_ip import detect, resolve


class PublicIPDetection(unittest.TestCase):
    def test_rejects_bad_results_and_uses_fallback(self):
        for bad in ['10.0.0.1', '127.0.0.1', '224.0.0.1', '::1', '<html>error</html>']:
            run = Mock(side_effect=[subprocess.CompletedProcess([], 0, bad),
                                    subprocess.CompletedProcess([], 0, '8.8.8.8\n')])
            self.assertEqual(detect(run), '8.8.8.8')
            self.assertEqual(run.call_count, 2)

    def test_timeouts_fail_with_bounded_attempts(self):
        run = Mock(side_effect=subprocess.TimeoutExpired('curl', 7))
        with self.assertRaises(ValueError):
            detect(run)
        self.assertEqual(run.call_count, 2)

    def test_override_and_existing_identity_skip_network(self):
        with tempfile.TemporaryDirectory() as folder:
            detector = Mock(side_effect=AssertionError('Network should not be used'))
            (Path(folder) / 'state.json').write_text(json.dumps({'public_ip': '8.8.4.4'}))
            self.assertEqual(resolve(folder, {}, detector), '8.8.4.4')
            self.assertEqual(resolve(folder, {'PUBLIC_IP': '1.1.1.1'}, detector), '1.1.1.1')

    def test_success_requires_no_input_and_failure_explains_override(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('sys.stdin') as stdin:
                stdin.isatty.return_value = False
                self.assertEqual(resolve(folder, {}, lambda: '8.8.8.8'), '8.8.8.8')
                stdin.readline.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'PUBLIC_IP'):
                    resolve(folder, {}, Mock(side_effect=ValueError()))

    def test_interactive_failure_accepts_manual_ipv4(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdin') as stdin:
            stdin.isatty.return_value = True
            stdin.readline.return_value = '8.8.4.4\n'
            self.assertEqual(resolve(folder, {}, Mock(side_effect=ValueError())), '8.8.4.4')
