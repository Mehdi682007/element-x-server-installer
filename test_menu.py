import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipIf(os.name == 'nt' or not shutil.which('bash'), 'Requires POSIX bash')
class MenuVersionConsistency(unittest.TestCase):
    def test_downloaded_menu_uses_matching_helpers_with_old_runtime(self):
        source = Path(__file__).with_name('menu.sh').read_text()
        # Load only functions, without invoking the privileged interactive main loop.
        functions = source.split('if [[ ${1:-} == --preview ]]; then')[0]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime, bundle = base / 'runtime', base / 'bundle'
            runtime.mkdir()
            bundle.mkdir()
            (runtime / 'state.json').write_text('{}')
            for name in ('accounts.py', 'manager.py'):
                (runtime / name).write_text('raise SystemExit(42)\n')
                (bundle / name).write_text('import sys\nprint("MATCHING_HELPER:" + sys.argv[1])\n')
            harness = bundle / 'check.sh'
            harness.write_text(functions + '\nROOT="$1"\nprint_header() { :; }\n'
                               'user_action admin-settings\noperation health\n')
            result = subprocess.run(['bash', str(harness), str(runtime)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('MATCHING_HELPER:admin-settings', result.stdout)
            self.assertIn('MATCHING_HELPER:health', result.stdout)
