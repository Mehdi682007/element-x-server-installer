#!/usr/bin/env python3
"""Resolve installation IPv4 without sending installation data to lookup services."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

SERVICES = ('https://api4.ipify.org', 'https://checkip.amazonaws.com')


def ipv4(value, public=False):
    address = ipaddress.IPv4Address(value.strip())
    if public and (not address.is_global or address.is_multicast):
        raise ValueError('Expected a public IPv4 address')
    return str(address)


def detect(run=subprocess.run):
    for url in SERVICES:
        try:
            result = run(['curl', '-4', '--noproxy', '*', '--fail', '--silent',
                          '--connect-timeout', '3', '--max-time', '5', url],
                         capture_output=True, text=True, timeout=7, check=True)
            return ipv4(result.stdout, public=True)
        except (ValueError, OSError, subprocess.SubprocessError):
            continue
    raise ValueError('Could not detect the public IPv4 address')


def resolve(root=Path('/opt/element-stack'), environ=None, detector=detect):
    environ = os.environ if environ is None else environ
    if environ.get('PUBLIC_IP'):
        return ipv4(environ['PUBLIC_IP'])
    state = Path(root) / 'state.json'
    if state.exists():
        return ipv4(json.loads(state.read_text())['public_ip'])
    try:
        address = detector()
        print('Detected public IPv4: ' + address, file=sys.stderr)
        return address
    except ValueError:
        if not sys.stdin.isatty():
            raise ValueError('IPv4 detection failed. Retry or set PUBLIC_IP explicitly.') from None
        print('Automatic detection failed. Enter server public IPv4: ', end='', file=sys.stderr, flush=True)
        return ipv4(sys.stdin.readline(), public=True)


if __name__ == '__main__':
    try:
        print(resolve())
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
