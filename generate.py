#!/usr/bin/env python3
"""Generate private, per-installation config. Never commit the output directory."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import yaml


def generate(root, domain, public_ip):
    domain = domain.lower().strip().rstrip('.')
    if len(domain) > 180 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        raise ValueError('Use a plain DNS domain, for example example.com')
    ipaddress.IPv4Address(public_ip)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    state_file = root / 'state.json'
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state['domain'] != domain or state['public_ip'] != public_ip:
            raise ValueError('An existing installation cannot silently change domain/IP')
    else:
        state = {'domain': domain, 'public_ip': public_ip}
        for key in ['database_password', 'registration_secret', 'macaroon_secret', 'form_secret', 'livekit_secret', 'as_token', 'hs_token']:
            state[key] = secrets.token_hex(32)
        state['livekit_key'] = 'LK' + secrets.token_hex(12)
        state_file.write_text(json.dumps(state, indent=2) + '\n')
        os.chmod(state_file, 0o600)

    def write(name, data, mode=0o600):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False))
        os.chmod(path, mode)

    matrix, web, rtc, turn = [f'{prefix}.{domain}' for prefix in ['matrix', 'element', 'rtc', 'turn']]
    registration = {
        'id': 'element-livekit', 'as_token': state['as_token'], 'hs_token': state['hs_token'],
        'sender_localpart': '_livekit', 'url': None,
        'namespaces': {'users': [{'exclusive': False, 'regex': '.*'}]},
        'io.element.msc4502.scopes': ['urn:matrix:client:io.element.msc4502:rooms:is_joined'],
        'io.element.msc4512.proxy_prefix': 'rtc/livekit',
        'io.element.msc4512.proxy_url': 'http://jwt:8080',
    }
    write('synapse/livekit-registration.yaml', registration, 0o644)
    write('synapse/homeserver.yaml', {
        'server_name': matrix, 'public_baseurl': f'https://{matrix}/', 'report_stats': False,
        'pid_file': '/data/homeserver.pid',
        'listeners': [{'port': 8008, 'tls': False, 'type': 'http', 'x_forwarded': True,
                       'resources': [{'names': ['client', 'federation'], 'compress': False}]}],
        'database': {'name': 'psycopg2', 'args': {'user': 'synapse', 'password': state['database_password'],
                    'database': 'synapse', 'host': 'postgres', 'cp_min': 5, 'cp_max': 10}},
        'media_store_path': '/data/media_store', 'max_upload_size': '50M',
        'signing_key_path': '/data/signing.key', 'log_config': '/data/log.config',
        'registration_shared_secret': state['registration_secret'],
        'macaroon_secret_key': state['macaroon_secret'], 'form_secret': state['form_secret'],
        'enable_registration': state.get('registration_open', False),
        'enable_registration_without_verification': state.get('registration_open', False),
        'experimental_features': {'msc3266_enabled': True, 'msc4143_enabled': True,
                                 'msc4222_enabled': True, 'msc4502_enabled': True, 'msc4512_enabled': True},
        'max_event_delay_duration': '24h',
        'rc_message': {'per_second': 0.5, 'burst_count': 30},
        'rc_delayed_event_mgmt': {'per_second': 1, 'burst_count': 20},
        'matrix_rtc': {'transports': [{'type': 'livekit', 'livekit_service_url': f'https://{rtc}/livekit/jwt'}]},
        'app_service_config_files': ['/data/livekit-registration.yaml'],
        'trusted_key_servers': [{'server_name': 'matrix.org'}],
    }, 0o644)
    write('synapse/log.config', {
        'version': 1,
        'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
        'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
        'root': {'level': 'INFO', 'handlers': ['console']},
        'disable_existing_loggers': False,
    }, 0o644)
    write('livekit.yaml', {
        'port': 7880, 'bind_addresses': ['127.0.0.1'],
        'rtc': {'tcp_port': 7881, 'port_range_start': 50000, 'port_range_end': 50100,
                'use_external_ip': False, 'node_ip': public_ip},
        'keys': {state['livekit_key']: state['livekit_secret']},
        'room': {'auto_create': False, 'empty_timeout': 300, 'departure_timeout': 20},
        'turn': {'enabled': True, 'domain': turn, 'udp_port': 3478, 'tls_port': 5349,
                 'proxy_protocol': True, 'proxy_protocol_trusted_cidrs': ['127.0.0.1/32'],
                 'cert_file': '/certs/fullchain.pem', 'key_file': '/certs/privkey.pem',
                 'relay_range_start': 50101, 'relay_range_end': 50200},
        'webhook': {'api_key': state['livekit_key'], 'urls': ['http://127.0.0.1:18080/sfu_webhook']},
        'logging': {'level': 'info'},
    })
    write('element-config.json', json.dumps({
        'default_server_config': {'m.homeserver': {'base_url': f'https://{matrix}', 'server_name': matrix}},
        'disable_custom_urls': True, 'disable_guests': True, 'brand': 'Element',
        'default_theme': 'dark', 'room_directory': {'servers': [matrix]},
        'features': {'feature_group_calls': True},
        'element_call': {'use_exclusively': True},
    }, indent=2), 0o644)

    logging = {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}
    def service(image, **options):
        return {'image': image, 'restart': 'unless-stopped', 'logging': logging,
                'security_opt': ['no-new-privileges:true'], **options}
    write('compose.yaml', {'name': 'element-stack', 'services': {
        'postgres': service('postgres:17-alpine',
            environment={'POSTGRES_USER': 'synapse', 'POSTGRES_DB': 'synapse',
                         'POSTGRES_PASSWORD': state['database_password'],
                         'POSTGRES_INITDB_ARGS': '--encoding=UTF8 --locale=C'},
            volumes=['./postgres:/var/lib/postgresql/data'],
            healthcheck={'test': ['CMD-SHELL', 'pg_isready -U synapse -d synapse'], 'interval': '10s', 'timeout': '5s', 'retries': 12}),
        'synapse': service('ghcr.io/element-hq/synapse:v1.163.2',
            volumes=['./synapse:/data'], ports=['127.0.0.1:8008:8008'],
            depends_on={'postgres': {'condition': 'service_healthy'}},
            healthcheck={'test': ['CMD', 'curl', '-fSs', 'http://localhost:8008/health'], 'interval': '15s', 'timeout': '5s', 'retries': 20, 'start_period': '30s'}),
        'jwt': service('ghcr.io/element-hq/lk-jwt-service:0.9.0',
            environment={'LIVEKIT_URL': f'wss://{rtc}/livekit/sfu', 'LIVEKIT_KEY': state['livekit_key'],
                         'LIVEKIT_SECRET': state['livekit_secret'], 'LIVEKIT_FULL_ACCESS_HOMESERVERS': matrix,
                         'LIVEKIT_HS_SERVER_NAME': matrix, 'LIVEKIT_AS_REGISTRATION_FILE': '/registration.yaml',
                         'LIVEKIT_SANITY_CHECK_INTERVAL_SECONDS': '60'},
            volumes=['./synapse/livekit-registration.yaml:/registration.yaml:ro'],
            ports=['127.0.0.1:18080:8080']),
        'livekit': service('livekit/livekit-server:v1.13.11', network_mode='host',
            command=['--config', '/etc/livekit.yaml'],
            volumes=['./livekit.yaml:/etc/livekit.yaml:ro', './certs:/certs:ro']),
        'element': service('ghcr.io/element-hq/element-web:v1.12.32',
            volumes=['./element-config.json:/app/config.json:ro'], ports=['127.0.0.1:18088:80']),
    }})
    names = f'{matrix} {web} {rtc} {turn}'
    http = f'''server {{
    listen 80;
    listen [::]:80;
    server_name {names};
    location ^~ /.well-known/acme-challenge/ {{ root /var/www/element-acme; }}
    location / {{ return 301 https://$host$request_uri; }}
}}
'''
    write('nginx-http.conf', http)
    tls = f'''listen 127.0.0.1:8443 ssl proxy_protocol;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;
    ssl_certificate /etc/letsencrypt/live/{matrix}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{matrix}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 50m;
'''
    headers = '''proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
'''
    client_discovery = json.dumps({'m.homeserver': {'base_url': f'https://{matrix}'},
                                  'org.matrix.msc4143.rtc_foci': [{'type': 'livekit', 'livekit_service_url': f'https://{rtc}/livekit/jwt'}]})
    server_discovery = json.dumps({'m.server': f'{matrix}:443'})
    write('nginx.conf', http + f'''
server {{
    {tls}
    server_name {matrix};
    location = /.well-known/matrix/client {{
        default_type application/json;
        add_header Access-Control-Allow-Origin * always;
        return 200 '{client_discovery}';
    }}
    location = /.well-known/matrix/server {{
        default_type application/json;
        return 200 '{server_discovery}';
    }}
    location ~ ^(/_matrix|/_synapse/client) {{
        {headers}
        proxy_read_timeout 600s;
        proxy_pass http://127.0.0.1:8008;
    }}
    location / {{ return 404; }}
}}
server {{
    {tls}
    server_name {web};
    location / {{
        {headers}
        proxy_pass http://127.0.0.1:18088;
    }}
}}
server {{
    {tls}
    server_name {rtc};
    location ^~ /livekit/jwt/ {{
        {headers}
        proxy_pass http://127.0.0.1:18080/;
    }}
    location ^~ /livekit/sfu/ {{
        {headers}
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_buffering off;
        proxy_pass http://127.0.0.1:7880/;
    }}
    location / {{ return 404; }}
}}
''')
    write('nginx-stream.conf', f'''# Loaded at nginx main context, after the stream module.
stream {{
    map $ssl_preread_server_name $element_tls_backend {{
        {turn} 127.0.0.1:5349;
        default 127.0.0.1:8443;
    }}
    server {{
        listen 443;
        listen [::]:443;
        ssl_preread on;
        proxy_protocol on;
        proxy_pass $element_tls_backend;
        proxy_connect_timeout 10s;
        proxy_timeout 1h;
    }}
}}
''')
    print(f'Configuration ready: {root} (secrets preserved on re-run)')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--directory', required=True)
    p.add_argument('--domain', required=True)
    p.add_argument('--public-ip', required=True)
    args = p.parse_args()
    generate(args.directory, args.domain, args.public_ip)
