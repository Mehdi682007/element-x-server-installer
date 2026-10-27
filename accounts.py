#!/usr/bin/env python3
"""Local Synapse user administration; never modify password hashes directly."""
import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import yaml


class APIError(RuntimeError):
    def __init__(self, status, code):
        self.status = status
        super().__init__(f'Synapse HTTP {status}: {code}')


class Accounts:
    def __init__(self, root='/opt/element-stack', base='http://127.0.0.1:8008'):
        self.root, self.base = Path(root), base
        self.config = yaml.safe_load((self.root / 'synapse/homeserver.yaml').read_text())
        self.server = self.config['server_name']
        self.token = None
        self.admin_id = None

    def request(self, method, path, data=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(self.base + path,
            None if data is None else json.dumps(data).encode(), headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                code = json.load(error).get('errcode', 'request_failed')
            except (ValueError, AttributeError):
                code = 'request_failed'
            # Do not echo request bodies, access tokens or server-supplied terminal controls.
            raise APIError(error.code, re.sub(r'[^A-Za-z0-9_]', '', code)) from None

    def mxid(self, name):
        if name.startswith('@'):
            local, separator, server = name[1:].partition(':')
            if not separator or server != self.server:
                raise ValueError('Only local accounts on this server can be managed')
        else:
            local = name
        if not re.fullmatch(r'[a-z0-9][a-z0-9._=/-]{0,99}', local):
            raise ValueError('Use a lowercase username (letters, numbers, . _ = / -)')
        return f'@{local}:{self.server}'

    def create(self, name, password, admin=False):
        user_id = self.mxid(name)
        local = user_id[1:].split(':', 1)[0]
        path = '/_synapse/admin/v1/register'
        nonce = self.request('GET', path)['nonce']
        mac = hmac.new(self.config['registration_shared_secret'].encode(), digestmod=hashlib.sha1)
        mac.update('\0'.join([nonce, local, password, 'admin' if admin else 'notadmin']).encode())
        result = self.request('POST', path, {'nonce': nonce, 'username': local,
            'password': password, 'admin': admin, 'mac': mac.hexdigest()})
        # Creation returns a session; the management tool does not need to retain it.
        self.request('POST', '/_matrix/client/v3/logout', {}, result['access_token'])
        return result['user_id']

    def login(self, user, password):
        result = self.request('POST', '/_matrix/client/v3/login', {'type': 'm.login.password',
            'identifier': {'type': 'm.id.user', 'user': user}, 'password': password,
            'initial_device_display_name': 'PD-Element X Manager'})
        self.token, self.admin_id = result['access_token'], result['user_id']
        try:
            info = self.request('GET', '/_synapse/admin/v2/users/' + urllib.parse.quote(self.admin_id, safe=''), token=self.token)
            if not info.get('admin'):
                raise ValueError('A server administrator account is required')
        except BaseException:
            self.logout()
            raise

    def authenticate(self):
        saved = self.root / 'admin-credentials.txt'
        if saved.exists():
            fields = dict(line.split(': ', 1) for line in saved.read_text().splitlines() if ': ' in line)
            try:
                self.login(fields['User'], fields['Password'])
                return
            except (APIError, KeyError, ValueError):
                print('Saved initial admin credentials are no longer valid. Sign in below.')
        user = input('Admin MXID or username: ').strip()
        self.login(user, getpass.getpass('Admin password: '))

    def logout(self):
        if self.token:
            try:
                self.request('POST', '/_matrix/client/v3/logout', {}, self.token)
            except APIError as error:
                if error.status != 401:
                    raise
            finally:
                self.token = None

    def list_users(self):
        start = '0'
        seen = set()
        while start not in seen:
            seen.add(start)
            params = urllib.parse.urlencode({'from': start, 'limit': 100, 'deactivated': 'true'})
            result = self.request('GET', '/_synapse/admin/v2/users?' + params, token=self.token)
            yield from result['users']
            if 'next_token' not in result:
                return
            start = str(result['next_token'])
        raise RuntimeError('Unexpected repeated pagination token')

    def get_user(self, name):
        return self.request('GET', '/_synapse/admin/v2/users/' + urllib.parse.quote(self.mxid(name), safe=''), token=self.token)

    def guard_admin(self, user_id):
        if user_id == self.admin_id:
            raise ValueError('Sign in with another admin before modifying the current admin account')
        user = self.get_user(user_id)
        if user.get('admin') and not user.get('deactivated'):
            active_admins = [u for u in self.list_users() if u.get('admin') and not u.get('deactivated')]
            if len(active_admins) <= 1:
                raise ValueError('Refusing to remove the last active server administrator')

    def deactivate(self, name):
        user_id = self.mxid(name)
        self.guard_admin(user_id)
        return self.request('POST', '/_synapse/admin/v1/deactivate/' + urllib.parse.quote(user_id, safe=''),
                            {'erase': False}, self.token)

    def reactivate(self, name, password, admin=False):
        user_id = self.mxid(name)
        self.check_reactivation(user_id)
        self.guard_admin(user_id)
        return self.request('PUT', '/_synapse/admin/v2/users/' + urllib.parse.quote(user_id, safe=''),
            {'password': password, 'admin': admin, 'deactivated': False, 'logout_devices': True}, self.token)

    def check_reactivation(self, name):
        if not self.get_user(name).get('deactivated'):
            raise ValueError('This account is already active. For admin password changes use menu option 17.')

    def save_credentials(self, name, password):
        user_id = self.mxid(name)
        domain = json.loads((self.root / 'state.json').read_text())['domain']
        data = (f'Homeserver: https://matrix.{domain}\nWeb: https://element.{domain}\n'
                f'User: {user_id}\nPassword: {password}\n')
        fd, temporary = tempfile.mkstemp(prefix='.admin-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.root / 'admin-credentials.txt')
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def change_admin_password(self, name, password):
        user_id = self.mxid(name)
        info = self.get_user(user_id)
        if not info.get('admin') or info.get('deactivated'):
            raise ValueError('Choose an active administrator account')
        self.request('POST', '/_synapse/admin/v1/reset_password/' + urllib.parse.quote(user_id, safe=''),
                     {'new_password': password, 'logout_devices': True}, self.token)
        self.save_credentials(user_id, password)
        if user_id == self.admin_id:
            self.logout()  # Synapse may preserve the session making the reset request.


def admin_settings(api):
    print('1) Show saved administrator login details')
    print('2) Change administrator password (requires an admin login)')
    print('3) Create a new primary admin username (also works if password is forgotten)')
    print('4) Back')
    choice = input('Choose [1-4]: ').strip()
    if choice == '1':
        path = api.root / 'admin-credentials.txt'
        if not path.exists():
            raise ValueError('No saved credentials. Use option 3 to create a new administrator.')
        print('Saved credentials; a password changed outside this tool may differ.')
        print(path.read_text())
        print('Keep these details private. They are not copied to the menu log.')
    elif choice == '2':
        api.authenticate()
        name = input(f'Admin username [Enter for {api.admin_id}]: ').strip() or api.admin_id
        info = api.get_user(name)
        if not info.get('admin') or info.get('deactivated'):
            raise ValueError('Choose an active administrator account')
        print('All sessions of this account will be logged out. Keep your Recovery Key for encrypted history.')
        password = prompt_password()
        if input('Change password and save this as the primary admin? (y/n): ').lower() != 'y':
            print('Cancelled.'); return
        api.change_admin_password(name, password)
        print('✅ Password changed and saved in root-only admin-credentials.txt.')
    elif choice == '3':
        print('Matrix usernames cannot be renamed. This creates a separate ADMIN account.')
        print('The old account remains unchanged; messages, room memberships and keys are not transferred.')
        name = input('New admin username: ').strip()
        api.mxid(name)
        password = prompt_password()
        if input('Create and save this as the primary admin? (y/n): ').lower() != 'y':
            print('Cancelled.'); return
        user_id = api.create(name, password, admin=True)
        api.save_credentials(user_id, password)
        print('✅ New administrator created: ' + user_id)
        print('Login details saved in root-only admin-credentials.txt. Existing accounts are unchanged.')
    elif choice != '4':
        raise ValueError('Invalid option')


def prompt_password():
    password = getpass.getpass('New password: ')
    if len(password) < 8:
        raise ValueError('Use a password of at least 8 characters')
    if any(c in password for c in '\r\n\0'):
        raise ValueError('Password cannot contain line breaks or null characters')
    if password != getpass.getpass('Confirm password: '):
        raise ValueError('Passwords do not match')
    return password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['create', 'list', 'reactivate', 'deactivate', 'admin-settings'])
    parser.add_argument('--admin', action='store_true')
    parser.add_argument('--random-password', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Run with sudo/root')
    os.umask(0o077)
    api = Accounts()
    try:
        if args.action == 'admin-settings':
            admin_settings(api)
            return
        if args.action == 'create':
            name = input('Enter username (localpart): ').strip()
            password = secrets.token_urlsafe(24) if args.random_password else prompt_password()
            user_id = api.create(name, password, args.admin)
            print('✅ Created ' + ('ADMIN' if args.admin else 'NORMAL') + ' user: ' + user_id)
            if args.random_password:
                print('Password: ' + password)
                print('Save this password now. It is not written to the manager log.')
            return
        api.authenticate()
        if args.action == 'list':
            print('MXID | admin(1/0) | deactivated(1/0)')
            print('-------------------------------------------')
            for user in api.list_users():
                name = ''.join(c for c in user['name'] if c.isprintable())
                print(f"{name} | {int(user['admin'])} | {int(user['deactivated'])}")
        else:
            name = input('Enter local username or full MXID: ').strip()
            user_id = api.mxid(name)
            if args.action == 'deactivate':
                print('Existing sessions will be revoked. Messages/rooms are not hard-deleted.')
                if input(f'Deactivate {user_id}? (y/n): ').lower() != 'y':
                    print('Cancelled.'); return
                api.deactivate(name)
                print('✅ User deactivated.')
            else:
                api.check_reactivation(name)
                api.reactivate(name, prompt_password(), args.admin)
                print('✅ User reactivated with a new password. Previous room memberships are not restored automatically.')
    finally:
        api.logout()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, EOFError) as error:
        print('❌ ' + str(error), file=sys.stderr)
        sys.exit(1)
