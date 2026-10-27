import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
import yaml
from accounts import Accounts
from generate import generate
from manager import Manager, extract_safe, upload_size, element_version


class Fixture:
    def __init__(self, folder):
        self.base = Path(folder)
        self.root = self.base / 'runtime'
        generate(self.root, 'example.com', '203.0.113.10')
        (self.root / 'synapse/signing.key').write_text('test-signing-key')
        for helper in ['manage.sh', 'menu.sh', 'manager.py', 'accounts.py', 'registration.py', 'create_admin.py']:
            (self.root / helper).write_text('test-helper')
        self.calls = []
        self.fail_restore = False
        self.manager = Manager(self.root, run=self.run)
        self.manager.backups = self.base / 'backups'
        nginx = self.base / 'nginx'
        nginx.mkdir()
        self.manager.nginx = nginx / 'site'
        self.manager.stream = nginx / 'stream'
        self.manager.link = nginx / 'enabled'
        shutil.copyfile(self.root / 'nginx.conf', self.manager.nginx)
        shutil.copyfile(self.root / 'nginx-stream.conf', self.manager.stream)
        self.manager.link.symlink_to(self.manager.nginx)
        self.manager.health = lambda **kwargs: None
        self.manager.copy_certificates = lambda: None

    def run(self, args, **kwargs):
        self.calls.append(args)
        if self.fail_restore and 'pg_restore' in args and '--exit-on-error' in args:
            raise subprocess.CalledProcessError(1, args)
        if 'pg_dump' in args:
            kwargs['stdout'].write(b'PGDMP-test-fixture')
        return types.SimpleNamespace(stdout='1024\n', returncode=0)


class OperationsSafety(unittest.TestCase):
    def test_archive_rejects_traversal_symlinks_and_duplicates(self):
        for kind in ['traversal', 'symlink', 'duplicate']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as tar:
                    info = tarfile.TarInfo('../escape' if kind == 'traversal' else 'file')
                    if kind == 'symlink':
                        info.type = tarfile.SYMTYPE
                        info.linkname = '/etc'
                    tar.addfile(info)
                    if kind == 'duplicate':
                        tar.addfile(info)
                with self.assertRaises(ValueError):
                    extract_safe(archive, Path(folder) / 'out')
                self.assertFalse((Path(folder) / 'escape').exists())

    def test_upload_and_web_version_survive_regeneration(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder)
            config_before = (fixture.root / 'element-config.json').read_bytes()
            fixture.manager.set_upload(250)
            fixture.manager.update_element('1.12.32')
            generate(fixture.root, 'example.com', '203.0.113.10')
            self.assertEqual(config_before, (fixture.root / 'element-config.json').read_bytes())
            self.assertEqual(yaml.safe_load((fixture.root / 'synapse/homeserver.yaml').read_text())['max_upload_size'], '250M')
            self.assertIn('client_max_body_size 250M;', (fixture.root / 'nginx.conf').read_text())
            self.assertEqual(json.loads((fixture.root / 'state.json').read_text())['element_version'], 'v1.12.32')

    def test_backup_check_and_restore_preserve_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder)
            archive = fixture.manager.backup(include_tls=False)
            fixture.manager.restore(archive, check_only=True)
            key = (fixture.root / 'synapse/signing.key').read_bytes()
            (fixture.root / 'post-backup-file').write_text('created after snapshot')
            with patch('builtins.input', return_value='RESTORE'), patch('manager.os.chown', create=True):
                fixture.manager.restore(archive)
            self.assertFalse((fixture.root / 'post-backup-file').exists())
            self.assertEqual(key, (fixture.root / 'synapse/signing.key').read_bytes())
            self.assertEqual(len(list(fixture.base.glob('element-stack.previous-*'))), 1)

    def test_restore_failure_restores_previous_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder)
            archive = fixture.manager.backup()
            (fixture.root / 'keep-me').write_text('current production data')
            fixture.fail_restore = True
            with patch('builtins.input', return_value='RESTORE'), patch('manager.os.chown', create=True):
                with self.assertRaises(subprocess.CalledProcessError):
                    fixture.manager.restore(archive)
            self.assertEqual((fixture.root / 'keep-me').read_text(), 'current production data')
            self.assertEqual(len(list(fixture.base.glob('element-stack.failed-*'))), 1)

    def test_restore_cancel_does_not_stop_services(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder)
            archive = fixture.manager.backup()
            fixture.calls.clear()
            with patch('builtins.input', return_value='no'):
                fixture.manager.restore(archive)
            self.assertFalse(any('down' in call or 'stop' in call for call in fixture.calls))

    def test_purge_refuses_any_other_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder)
            with self.assertRaises(ValueError):
                fixture.manager.purge()
            self.assertTrue(fixture.root.exists())
            self.assertFalse(fixture.calls)

    def test_reject_unbounded_limits_and_version_injection(self):
        for value in ['0', '-1', '10241', '1;echo x', '50M']:
            with self.assertRaises(ValueError):
                upload_size(value)
        for value in ['latest; id', '../bad', 'v1.2.3\nfoo']:
            with self.assertRaises(ValueError):
                element_version(value)


class UserSafety(unittest.TestCase):
    def api(self, folder):
        generate(folder, 'example.com', '203.0.113.10')
        return Accounts(folder)

    def test_deactivation_uses_api_and_refuses_current_admin(self):
        with tempfile.TemporaryDirectory() as folder:
            api = self.api(folder)
            api.admin_id = '@admin:matrix.example.com'
            api.token = 'test-token'
            with self.assertRaises(ValueError):
                api.deactivate('admin')
            api.get_user = lambda name: {'admin': False}
            with patch.object(api, 'request', return_value={}) as request:
                api.deactivate('alice')
                args = request.call_args.args
                self.assertEqual(args[0], 'POST')
                self.assertIn('/deactivate/', args[1])
                self.assertEqual(args[2], {'erase': False})

    def test_last_admin_and_remote_user_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            api = self.api(folder)
            api.admin_id = '@operator:matrix.example.com'
            api.get_user = lambda name: {'admin': True, 'deactivated': False}
            api.list_users = lambda: iter([{'admin': True, 'deactivated': False}])
            with self.assertRaises(ValueError):
                api.deactivate('admin')
            with self.assertRaises(ValueError):
                api.mxid('@alice:remote.example')


if __name__ == '__main__':
    unittest.main()
