#!/usr/bin/env python3
"""Change registration without regenerating unrelated server configuration."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import yaml


def replace_file(path, contents):
    metadata = path.stat()
    fd, temporary = tempfile.mkstemp(prefix='.registration-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777)
        if hasattr(os, 'chown'):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def change(root, action, run=subprocess.run):
    root = Path(root)
    config_path = root / 'synapse/homeserver.yaml'
    state_path = root / 'state.json'
    original_config, original_state = config_path.read_bytes(), state_path.read_bytes()
    config, state = yaml.safe_load(original_config), json.loads(original_state)
    if action == 'status':
        print('Public registration: ' + ('OPEN' if config.get('enable_registration', False) else 'CLOSED'))
        return
    if action not in ('open', 'close'):
        raise ValueError('Use open, close or status')
    enabled = action == 'open'
    config['enable_registration'] = enabled
    config['enable_registration_without_verification'] = enabled
    state['registration_open'] = enabled
    restart_started = False
    def docker(*args):
        return run(['docker', 'compose', *args], cwd=root, check=True)
    try:
        replace_file(config_path, yaml.safe_dump(config, sort_keys=False).encode())
        docker('exec', '-T', 'synapse', 'python', '-m', 'synapse.config', '-c', '/data/homeserver.yaml')
        restart_started = True
        docker('restart', 'synapse')
        docker('up', '-d', '--wait', '--wait-timeout', '180', 'synapse')
        replace_file(state_path, (json.dumps(state, indent=2) + '\n').encode())
    except BaseException:
        replace_file(config_path, original_config)
        replace_file(state_path, original_state)
        if restart_started:
            docker('restart', 'synapse')
            docker('up', '-d', '--wait', '--wait-timeout', '180', 'synapse')
        raise
    print('Public registration: ' + ('OPEN' if enabled else 'CLOSED'))
    if enabled:
        print('Anyone can create an account. No email/CAPTCHA is configured by this installer.')
        print('Sign up in Element Web: https://element.' + state['domain'] + '/#/register')
        print('Then sign in to Element X with the new account.')
    else:
        print('Existing accounts still work. Administrators can still use manage.sh create-user.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['open', 'close', 'status'], nargs='?', default='status')
    args = parser.parse_args()
    change('/opt/element-stack', args.action)
