#!/usr/bin/env python3
"""PD-Element X operations. All mutations are limited to this installation."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import yaml
from registration import replace_file

HELPERS = ['manage.sh', 'menu.sh', 'manager.py', 'accounts.py', 'registration.py', 'create_admin.py']


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)


def upload_size(value):
    if not re.fullmatch(r'[1-9][0-9]{0,4}', str(value)) or not 1 <= int(value) <= 10240:
        raise ValueError('Upload limit must be between 1 and 10240 MiB')
    return int(value)


def element_version(value):
    value = value.removeprefix('v')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta)\.[0-9]+)?', value):
        raise ValueError('Enter a release version such as 1.12.32')
    return 'v' + value


def extract_safe(archive, destination, outer=False):
    """Extract regular files/directories only; no links, traversal or special nodes."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    seen, total = set(), 0
    available = shutil.disk_usage(destination).free
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Unsafe archive path')
            if not member.isfile() and not member.isdir():
                raise ValueError('Links and special files are not allowed in a backup')
            if str(path) == '.' and member.isdir():
                continue
            if str(path) in seen or len(seen) >= 100000:
                raise ValueError('Duplicate path or too many backup members')
            seen.add(str(path))
            if outer and str(path) not in ('database.dump', 'files.tar.gz', 'backup-manifest.json'):
                raise ValueError('Not a PD-Element X backup (legacy native/SQLite backups need migration)')
            if not outer and path.parts[0] in ('postgres', 'config-history'):
                raise ValueError('Unexpected physical database or history in logical backup')
            total += member.size
            if total + 128 * 1024**2 > available:
                raise ValueError('Not enough free space to stage this backup')
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if member.isdir():
                target.mkdir(exist_ok=True, mode=0o700)
            else:
                with tar.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o777)
    if outer and not {'database.dump', 'files.tar.gz'} <= seen:
        raise ValueError('Missing database.dump or files.tar.gz')
    return total


class Manager:
    def __init__(self, root='/opt/element-stack', run=subprocess.run):
        self.root = Path(root)
        self.run = run
        self.state = json.loads((self.root / 'state.json').read_text())
        self.domain = self.state['domain']
        self.backups = Path('/var/backups/element-stack')
        self.nginx = Path('/etc/nginx/sites-available/element-stack.conf')
        self.link = Path('/etc/nginx/sites-enabled/element-stack.conf')
        self.stream = Path('/etc/nginx/modules-enabled/99-element-stack-stream.conf')

    def command(self, *args, **kwargs):
        return self.run(list(args), check=True, **kwargs)

    def docker(self, *args, **kwargs):
        return self.command('docker', 'compose', *args, cwd=self.root, **kwargs)

    def save_state(self):
        replace_file(self.root / 'state.json', (json.dumps(self.state, indent=2) + '\n').encode())

    @contextmanager
    def transaction(self, paths, recover):
        originals = {p: p.read_bytes() for p in paths}
        try:
            yield
        except BaseException:
            for path, data in originals.items():
                replace_file(path, data)
            self.state = json.loads((self.root / 'state.json').read_text())
            recover()
            raise

    def synapse_ready(self):
        self.docker('restart', 'synapse')
        self.docker('up', '-d', '--wait', '--wait-timeout', '180', 'synapse')

    def set_upload(self, size):
        size = upload_size(size)
        config_path = self.root / 'synapse/homeserver.yaml'
        template = self.root / 'nginx.conf'
        paths = [config_path, template, self.nginx, self.root / 'state.json']
        def recover():
            self.command('nginx', '-t')
            self.command('systemctl', 'reload', 'nginx')
            self.synapse_ready()
        with self.transaction(paths, recover):
            config = yaml.safe_load(config_path.read_text())
            config['max_upload_size'] = f'{size}M'
            replace_file(config_path, yaml.safe_dump(config, sort_keys=False).encode())
            for path in [template, self.nginx]:
                text, count = re.subn(r'client_max_body_size\s+[^;]+;', f'client_max_body_size {size}M;', path.read_text())
                if not count:
                    raise ValueError('Missing client_max_body_size in managed nginx config')
                replace_file(path, text.encode())
            self.docker('exec', '-T', 'synapse', 'python', '-m', 'synapse.config', '-c', '/data/homeserver.yaml')
            self.command('nginx', '-t')
            self.command('systemctl', 'reload', 'nginx')
            self.synapse_ready()
            self.state['upload_limit_mb'] = size
            self.save_state()
        print(f'✅ Synapse and Nginx upload limit: {size} MiB (preserved on reinstall).')

    def update_element(self, version):
        if version == 'latest':
            with urllib.request.urlopen('https://api.github.com/repos/element-hq/element-web/releases/latest', timeout=30) as response:
                version = json.load(response)['tag_name']
        version = element_version(version)
        image = 'ghcr.io/element-hq/element-web:' + version
        print('Downloading official Element Web release: ' + version)
        self.command('docker', 'pull', image)
        compose = self.root / 'compose.yaml'
        def ready():
            self.docker('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'element')
        with self.transaction([compose, self.root / 'state.json'], ready):
            data = yaml.safe_load(compose.read_text())
            data['services']['element']['image'] = image
            replace_file(compose, yaml.safe_dump(data, sort_keys=False).encode())
            self.docker('config', '--quiet')
            ready()
            self.state['element_version'] = version
            self.save_state()
        print('✅ Element Web updated; config.json preserved. Update Element X on the phone through its app store.')

    def probe(self, name, url, json_body=False):
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                result = json.load(response) if json_body else response.read(1024)
            print('✅ ' + name)
            return result
        except Exception as error:
            print(f'❌ {name}: {type(error).__name__}')
            return None

    def health(self, calls=False):
        self.docker('ps')
        self.command('nginx', '-t')
        base = 'https://matrix.' + self.domain
        results = [self.probe('Matrix API', base + '/_matrix/client/versions', True),
                   self.probe('Client discovery', base + '/.well-known/matrix/client', True),
                   self.probe('Federation discovery', base + '/.well-known/matrix/server', True),
                   self.probe('Element Web', 'https://element.' + self.domain + '/config.json', True),
                   self.probe('MatrixRTC authorization', 'https://rtc.' + self.domain + '/livekit/jwt/healthz'),
                   self.probe('LiveKit local signaling', 'http://127.0.0.1:7880/')]
        host = 'turn.' + self.domain
        with socket.create_connection((host, 443), timeout=15) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as connection:
                connection.settimeout(15)
                print('🔐 TLS certificate expires: ' + connection.getpeercert()['notAfter'])
                transaction = secrets.token_bytes(12)
                connection.sendall(struct.pack('!HHI', 1, 0, 0x2112A442) + transaction)
                packet = b''
                while len(packet) < 20:
                    part = connection.recv(20 - len(packet))
                    if not part:
                        raise RuntimeError('TURN connection ended early')
                    packet += part
                if packet[:2] != b'\x01\x01' or packet[8:20] != transaction:
                    raise RuntimeError('TURN TLS STUN response was not valid')
                print('✅ TURN over TLS/443 binding response')
        if calls:
            config = yaml.safe_load((self.root / 'livekit.yaml').read_text())
            print('📞 MatrixRTC enabled:', bool((results[0] or {}).get('unstable_features', {}).get('org.matrix.msc4143')))
            print('📌 Media public IP:', config['rtc']['node_ip'])
            print('🧷 Required inbound: TCP 80,443,7881; UDP 3478,50000-50200')
            print('TURN TLS uses public 443; internal 5349 expects the trusted local proxy.')
            listening = self.command('ss', '-lntup', capture_output=True, text=True).stdout
            for line in listening.splitlines():
                if re.search(r':(?:80|443|3478|5349|7880|7881|8008|18080)\b', line):
                    print(line)
            if shutil.which('ufw'):
                self.command('ufw', 'status')
            print('No API keys or shared secrets are printed. Test two devices on different networks for end-to-end media.')
        if any(result is None for result in results):
            raise RuntimeError('One or more HTTP probes failed')

    def copy_certificates(self):
        lineage = Path('/etc/letsencrypt/live') / ('matrix.' + self.domain)
        (self.root / 'certs').mkdir(mode=0o700, exist_ok=True)
        for name, mode in [('fullchain.pem', 0o644), ('privkey.pem', 0o600)]:
            shutil.copyfile(lineage / name, self.root / 'certs' / name)
            os.chmod(self.root / 'certs' / name, mode)

    def install_nginx(self):
        shutil.copyfile(self.root / 'nginx.conf', self.nginx)
        os.chmod(self.nginx, 0o644)
        shutil.copyfile(self.root / 'nginx-stream.conf', self.stream)
        os.chmod(self.stream, 0o644)
        if self.link.is_symlink():
            if self.link.resolve() != self.nginx.resolve():
                raise ValueError('Managed symlink points at a different site; inspect manually')
        elif self.link.exists():
            raise ValueError('Managed site-enabled path is not a symlink; inspect manually')
        else:
            self.link.symlink_to(self.nginx)
        self.command('nginx', '-t')
        self.command('systemctl', 'reload', 'nginx')

    def repair(self):
        print('Repairing managed Nginx files, TLS copies and stack services. Active calls may briefly disconnect.')
        snapshots = {}
        for path in (self.nginx, self.stream):
            snapshots[path] = path.read_bytes() if path.exists() else None
        had_link = self.link.exists() or self.link.is_symlink()
        try:
            self.install_nginx()
        except BaseException:
            for path, contents in snapshots.items():
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(contents)
            if not had_link:
                self.link.unlink(missing_ok=True)
            raise
        self.copy_certificates()
        self.command('systemctl', 'enable', '--now', 'docker', 'nginx', 'certbot.timer')
        self.docker('up', '-d', '--wait', '--wait-timeout', '240')
        self.docker('restart', 'livekit')
        for attempt in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:7880/', timeout=2):
                    break
            except OSError:
                if attempt == 29:
                    raise RuntimeError('LiveKit did not become ready after restart')
                time.sleep(1)
        self.health()
        print('✅ Fix Wizard complete. Other nginx sites and registration settings were preserved.')

    def backup(self, include_tls=True):
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.backups, 0o700)
        result = self.backups / ('element-stack-' + stamp() + '.tar.gz')
        temporary_result = result.with_suffix('.partial')
        db_bytes = int(self.docker('exec', '-T', 'postgres', 'psql', '-U', 'synapse', '-d', 'synapse', '-Atc',
                                  "SELECT pg_database_size('synapse')", capture_output=True, text=True).stdout.strip())
        media_bytes = sum(p.stat().st_size for p in (self.root / 'synapse').rglob('*') if p.is_file())
        if shutil.disk_usage(self.backups).free < (db_bytes + media_bytes) * 2 + 128 * 1024**2:
            raise ValueError('Not enough free space to create a consistent backup')
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=self.backups) as directory:
            job = Path(directory)
            print('Pausing Synapse to take a consistent database/media backup...')
            try:
                self.docker('stop', 'synapse')
                with (job / 'database.dump').open('wb') as output:
                    self.docker('exec', '-T', 'postgres', 'pg_dump', '-U', 'synapse', '-Fc', 'synapse', stdout=output)
                def include(info):
                    top = PurePosixPath(info.name).parts
                    if top and (top[0] in ('postgres', 'config-history') or (top[0] == 'certs' and not include_tls)):
                        return None
                    if not info.isfile() and not info.isdir():
                        raise ValueError('Unexpected link/special file in runtime data; inspect before backup')
                    return info
                with tarfile.open(job / 'files.tar.gz', 'w:gz') as tar:
                    for path in self.root.iterdir():
                        tar.add(path, arcname=path.name, filter=include)
                (job / 'backup-manifest.json').write_text(json.dumps({'format': 1, 'domain': self.domain,
                    'database_bytes': db_bytes, 'tls_copy_included': include_tls}))
                with tarfile.open(temporary_result, 'w:gz') as tar:
                    for path in job.iterdir():
                        tar.add(path, arcname=path.name)
                os.chmod(temporary_result, 0o600)
                os.replace(temporary_result, result)
            finally:
                self.docker('up', '-d', '--wait', '--wait-timeout', '180', 'synapse')
                temporary_result.unlink(missing_ok=True)
        print('✅ Private backup: ' + str(result))
        print('Copy off-server. Existing backups are not deleted automatically.')
        return result

    def restore(self, archive, check_only=False):
        archive = Path(archive).resolve(strict=True)
        if self.root.is_symlink():
            raise ValueError('Refusing restore through a symlinked runtime directory')
        # Stage completely before stopping services or changing production files.
        with tempfile.TemporaryDirectory(prefix='.pd-restore-', dir=self.root.parent) as directory:
            job = Path(directory)
            extract_safe(archive, job / 'outer', outer=True)
            staged = job / 'runtime'
            extract_safe(job / 'outer/files.tar.gz', staged)
            saved = json.loads((staged / 'state.json').read_text())
            if saved['domain'] != self.domain or saved['public_ip'] != self.state['public_ip']:
                raise ValueError('Install the same domain/IP first; identity migration requires a separate procedure')
            config = yaml.safe_load((staged / 'compose.yaml').read_text())
            current = yaml.safe_load((self.root / 'compose.yaml').read_text())
            if config.get('name') != 'element-stack' or set(config['services']) != set(current['services']):
                raise ValueError('Unexpected stack layout in backup')
            if config['services']['postgres']['image'].split(':')[-1].split('-')[0].split('.')[0] != current['services']['postgres']['image'].split(':')[-1].split('-')[0].split('.')[0]:
                raise ValueError('PostgreSQL major versions must match for this restore')
            for required in ['synapse/homeserver.yaml', 'synapse/signing.key', 'nginx.conf', 'nginx-stream.conf', 'livekit.yaml', 'element-config.json']:
                if not (staged / required).is_file():
                    raise ValueError('Backup is missing required file: ' + required)
            with (job / 'outer/database.dump').open('rb') as source:
                self.docker('exec', '-T', 'postgres', 'pg_restore', '--list', stdin=source, stdout=subprocess.DEVNULL)
            if check_only:
                print('✅ Backup structure, identity and PostgreSQL archive verified. No services changed.')
                return
            print('This replaces stack accounts, database, media and configuration. Use only a trusted backup.')
            if input('Type RESTORE to continue: ') != 'RESTORE':
                print('Cancelled.'); return
            self.backup()
            for helper in HELPERS:
                shutil.copy2(self.root / helper, staged / helper)
            previous = self.root.with_name('element-stack.previous-' + stamp())
            failed = self.root.with_name('element-stack.failed-' + stamp())
            nginx_snapshot = {p: p.read_bytes() for p in (self.nginx, self.stream)}
            self.docker('down')
            self.root.rename(previous)
            try:
                staged.rename(self.root)
                os.chmod(self.root, 0o700)
                self.state = saved
                for path in [self.root / 'synapse', *(self.root / 'synapse').rglob('*')]:
                    os.chown(path, 991, 991)
                    if path.is_dir():
                        os.chmod(path, 0o755)
                self.copy_certificates()  # Keep the host's current certificate, not an expired snapshot.
                self.docker('config', '--quiet')
                self.docker('up', '-d', '--wait', '--wait-timeout', '180', 'postgres')
                with (job / 'outer/database.dump').open('rb') as source:
                    self.docker('exec', '-T', 'postgres', 'pg_restore', '-U', 'synapse', '-d', 'synapse', '--no-owner', '--exit-on-error', stdin=source)
                self.docker('up', '-d', '--wait', '--wait-timeout', '240')
                self.install_nginx()
                self.health()
            except BaseException:
                if self.root.exists():
                    self.run(['docker', 'compose', 'down'], cwd=self.root, check=False)
                    self.root.rename(failed)
                previous.rename(self.root)
                self.state = json.loads((self.root / 'state.json').read_text())
                for path, data in nginx_snapshot.items():
                    path.write_bytes(data)
                self.command('nginx', '-t')
                self.command('systemctl', 'reload', 'nginx')
                self.docker('up', '-d', '--wait', '--wait-timeout', '240')
                print('Restore failed; previous runtime/data restored. Failed files retained at: ' + str(failed))
                raise
            print('✅ Restore completed. Previous runtime retained at: ' + str(previous))

    def purge(self, dry_run=False):
        if self.root != Path('/opt/element-stack') or self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('Refusing purge outside the exact managed runtime path')
        print('Remove: element-stack containers/network, /opt/element-stack, and this stack’s nginx/renewal files.')
        print('Keep: other services, Docker/Nginx packages, installer bundle and /var/backups/element-stack.')
        if dry_run:
            print('Dry run only. No files, services or certificates changed.')
            return
        if input('Type DELETE to continue: ') != 'DELETE':
            print('Cancelled.'); return
        delete_cert = input('Also delete only this stack’s Let’s Encrypt certificate? (y/n): ').lower() == 'y'
        self.backup()
        snapshots = {p: p.read_bytes() if p.exists() else None for p in (self.nginx, self.stream)}
        had_link = self.link.is_symlink()
        if self.link.exists() and not had_link:
            raise ValueError('Unexpected non-symlink site entry; inspect manually')
        if had_link and self.link.resolve() != self.nginx.resolve():
            raise ValueError('Unexpected symlink target; inspect manually')
        try:
            self.link.unlink(missing_ok=True)
            self.nginx.unlink(missing_ok=True)
            self.stream.unlink(missing_ok=True)
            self.command('nginx', '-t')
            self.docker('down', '--remove-orphans')
            self.command('systemctl', 'reload', 'nginx')
        except BaseException:
            for path, data in snapshots.items():
                if data is not None:
                    path.write_bytes(data)
            if had_link and not self.link.is_symlink():
                self.link.symlink_to(self.nginx)
            self.docker('up', '-d', '--wait', '--wait-timeout', '240')
            raise
        Path('/etc/letsencrypt/renewal-hooks/deploy/element-stack').unlink(missing_ok=True)
        shutil.rmtree(self.root)
        if delete_cert:
            self.command('certbot', 'delete', '--non-interactive', '--cert-name', 'matrix.' + self.domain)
        print('✅ Stack purged. Backups and other host services preserved.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['upload-limit', 'update-element', 'health', 'diagnostics', 'fix', 'backup', 'restore', 'purge'])
    parser.add_argument('value', nargs='?')
    parser.add_argument('--without-tls', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Run with sudo/root')
    os.umask(0o077)
    manager = Manager()
    # Share the mutation lock with installation and registration controls.
    import fcntl
    with open('/run/element-stack-install.lock', 'w') as lock:
        if args.action not in ('health', 'diagnostics'):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'upload-limit':
            manager.set_upload(args.value or input('Upload limit (MiB): '))
        elif args.action == 'update-element':
            manager.update_element(args.value or input('Release version or latest: '))
        elif args.action in ('health', 'diagnostics'):
            manager.health(calls=args.action == 'diagnostics')
        elif args.action == 'fix':
            manager.repair()
        elif args.action == 'backup':
            manager.backup(include_tls=not args.without_tls)
        elif args.action == 'restore':
            if not args.value:
                for path in sorted(manager.backups.glob('*.tar.gz')):
                    print(path)
            manager.restore(args.value or input('Full path to trusted backup: '), check_only=args.check)
        elif args.action == 'purge':
            manager.purge(dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError, EOFError) as error:
        print('❌ ' + str(error), file=sys.stderr)
        sys.exit(1)
