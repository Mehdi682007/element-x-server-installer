#!/usr/bin/env python3
"""Local Synapse user administration; never modify password hashes directly."""
import argparse
import getpass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sys
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
        self.get_user(user_id)  # Reject a nonexistent account instead of creating one accidentally.
        self.guard_admin(user_id)
        return self.request('PUT', '/_synapse/admin/v2/users/' + urllib.parse.quote(user_id, safe=''),
            {'password': password, 'admin': admin, 'deactivated': False, 'logout_devices': True}, self.token)


def prompt_password():
    password = getpass.getpass('New password: ')
    if len(password) < 8:
        raise ValueError('Use a password of at least 8 characters')
    if password != getpass.getpass('Confirm password: '):
        raise ValueError('Passwords do not match')
    return password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['create', 'list', 'reactivate', 'deactivate'])
    parser.add_argument('--admin', action='store_true')
    parser.add_argument('--random-password', action='store_true')
    args = parser.parse_args()
    api = Accounts()
    try:
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
