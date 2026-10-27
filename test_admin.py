import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from accounts import Accounts, APIError, admin_settings


class AdminManagement(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / 'synapse').mkdir()
        (root / 'synapse/homeserver.yaml').write_text('server_name: matrix.example.com\n')
        (root / 'state.json').write_text(json.dumps({'domain': 'example.com'}))
        self.api = Accounts(root)
        self.api.admin_id = '@admin:matrix.example.com'
        self.api.token = 'temporary-session'
        self.api.get_user = Mock(return_value={'admin': True, 'deactivated': False})
        self.api.request = Mock(return_value={})

    def test_current_admin_password_reset_keeps_role_and_saves_credentials(self):
        self.api.change_admin_password('admin', 'new-private-password')
        args = self.api.request.call_args_list[0].args
        self.assertIn('/reset_password/', args[1])
        self.assertEqual(args[2], {'new_password': 'new-private-password', 'logout_devices': True})
        self.assertIsNone(self.api.token)
        self.assertEqual(self.api.request.call_args.args[1], '/_matrix/client/v3/logout')
        path = self.api.root / 'admin-credentials.txt'
        self.assertIn('User: @admin:matrix.example.com', path.read_text())
        self.assertIn('Password: new-private-password', path.read_text())
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_reset_does_not_change_saved_credentials(self):
        self.api.save_credentials('admin', 'old-private-password')
        self.api.request.side_effect = APIError(403, 'M_FORBIDDEN')
        with self.assertRaises(APIError):
            self.api.change_admin_password('admin', 'new-private-password')
        self.assertIn('Password: old-private-password', (self.api.root / 'admin-credentials.txt').read_text())
        self.assertEqual(self.api.token, 'temporary-session')

    def test_active_admin_reactivation_cannot_demote_or_reset(self):
        with self.assertRaisesRegex(ValueError, 'already active'):
            self.api.reactivate('admin', 'unused-password', admin=False)
        self.api.request.assert_not_called()

    def test_inactive_account_can_be_reactivated(self):
        self.api.get_user.return_value = {'admin': False, 'deactivated': True}
        self.api.reactivate('alice', 'new-private-password')
        self.assertFalse(self.api.request.call_args.args[2]['deactivated'])

    def test_new_primary_admin_does_not_authenticate_or_modify_old_account(self):
        self.api.authenticate = Mock(side_effect=AssertionError('No old password required'))
        self.api.create = Mock(return_value='@newadmin:matrix.example.com')
        with patch('builtins.input', side_effect=['3', 'newadmin', 'y']), patch('accounts.prompt_password', return_value='new-private-password'):
            admin_settings(self.api)
        self.api.create.assert_called_once_with('newadmin', 'new-private-password', admin=True)
        self.api.request.assert_not_called()
        self.assertIn('User: @newadmin:matrix.example.com', (self.api.root / 'admin-credentials.txt').read_text())

    def test_new_admin_cancel_preserves_credentials(self):
        self.api.save_credentials('admin', 'old-private-password')
        self.api.create = Mock()
        with patch('builtins.input', side_effect=['3', 'newadmin', 'n']), patch('accounts.prompt_password', return_value='new-private-password'):
            admin_settings(self.api)
        self.api.create.assert_not_called()
        self.assertIn('User: @admin:matrix.example.com', (self.api.root / 'admin-credentials.txt').read_text())
