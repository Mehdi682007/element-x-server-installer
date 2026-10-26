#!/usr/bin/env python3
"""Create the initial admin once; no password or access token is written to logs."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import urllib.request

root = Path('/opt/element-stack')
target = root / 'admin-credentials.txt'
if target.exists():
    print('Initial admin credential file already exists; account left unchanged.')
    raise SystemExit(0)
state = json.loads((root / 'state.json').read_text())
base = 'http://127.0.0.1:8008'
endpoint = base + '/_synapse/admin/v1/register'
with urllib.request.urlopen(endpoint, timeout=15) as response:
    nonce = json.load(response)['nonce']
password = secrets.token_urlsafe(24)
mac = hmac.new(state['registration_secret'].encode(), digestmod=hashlib.sha1)
mac.update('\0'.join([nonce, 'admin', password, 'admin']).encode())
payload = {'nonce': nonce, 'username': 'admin', 'password': password, 'admin': True, 'mac': mac.hexdigest()}
request = urllib.request.Request(endpoint, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=30) as response:
    account = json.load(response)
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as file:
    file.write(f"Homeserver: https://matrix.{state['domain']}\nWeb: https://element.{state['domain']}\nUser: {account['user_id']}\nPassword: {password}\n")
request = urllib.request.Request(base + '/_matrix/client/v3/logout', b'{}',
    {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + account['access_token']})
with urllib.request.urlopen(request, timeout=15):
    pass
print('Initial administrator created. Credentials saved in root-only admin-credentials.txt.')
