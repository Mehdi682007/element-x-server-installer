#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
trap 'echo "Installation stopped at line $LINENO. Fix the reported error and re-run; existing secrets are preserved." >&2' ERR
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=/opt/element-stack
DOMAIN=${DOMAIN:-}
PUBLIC_IP=${PUBLIC_IP:-}
EMAIL=${EMAIL:-}
[[ $EUID -eq 0 ]] || { echo 'Run with sudo bash install.sh'; exit 1; }
exec 9>/run/element-stack-install.lock
flock -n 9 || { echo 'Another installation is running.'; exit 1; }
[[ -n $DOMAIN ]] || read -rp 'Base domain (example.com): ' DOMAIN
[[ -n $PUBLIC_IP ]] || read -rp 'Server public IPv4: ' PUBLIC_IP
if [[ -z $EMAIL && -t 0 ]]; then
    read -rp 'Email for certificate expiry notices (optional): ' EMAIL
fi
export DOMAIN PUBLIC_IP
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == ubuntu && ( $VERSION_ID == 22.04 || $VERSION_ID == 24.04 ) ]] || {
    echo 'Supported OS: Ubuntu 22.04 / 24.04 LTS'; exit 1;
}
[[ $(dpkg --print-architecture) == amd64 || $(dpkg --print-architecture) == arm64 ]] || exit 1
python3 - <<'PY'
import os, re, ipaddress, socket
d = os.environ['DOMAIN']
assert re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', d), 'Invalid lowercase domain'
assert len(d) <= 180, 'Domain too long'
ip = str(ipaddress.IPv4Address(os.environ['PUBLIC_IP']))
for prefix in ['matrix', 'element', 'rtc', 'turn']:
    host = prefix + '.' + d
    resolved = {x[4][0] for x in socket.getaddrinfo(host, 80, socket.AF_INET)}
    assert resolved == {ip}, f'{host}: expected {ip}, found {resolved}. Disable DNS proxying.'
    try:
        v6 = socket.getaddrinfo(host, 80, socket.AF_INET6)
    except socket.gaierror:
        v6 = []
    assert not v6, f'Remove AAAA records for this IPv4-only installer: {host}'
print('DNS preflight passed')
PY
if [[ ! -e $ROOT/state.json ]]; then
    for port in 443 8008 8443 7880 7881 18080 18088 5349; do
        if ss -H -lnt "sport = :$port" | grep -q .; then
            echo "Port $port is already in use; integrate the existing service before installing."; exit 1
        fi
    done
    if ss -H -lnu 'sport = :3478' | grep -q .; then
        echo 'TURN UDP port 3478 is in use'; exit 1
    fi
    if ss -H -lnt 'sport = :80' | grep -q . && ! systemctl is-active --quiet nginx; then
        echo 'Port 80 is in use by a service other than the system nginx'; exit 1
    fi
    (( $(df --output=avail -k /opt | tail -1) >= 8*1024*1024 )) || {
        echo 'At least 8 GiB free disk space is required'; exit 1;
    }
    if [[ -e /etc/nginx/modules-enabled/99-element-stack-stream.conf || -e /etc/nginx/sites-available/element-stack.conf ]]; then
        echo 'Existing element-stack nginx files found without installation state; inspect manually.'; exit 1
    fi
    if command -v nginx >/dev/null && nginx -T 2>/dev/null | grep -Eq '^[[:space:]]*stream[[:space:]]*\{'; then
        echo 'An nginx stream block already exists; integrate SNI routing manually.'; exit 1
    fi
fi
export DEBIAN_FRONTEND=noninteractive
if [[ ${SKIP_PACKAGES:-0} != 1 ]]; then
    apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update
    apt-get -o DPkg::Lock::Timeout=120 install -y ca-certificates curl gnupg nginx libnginx-mod-stream certbot python3-yaml
    if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
        # Preserve forwarding on an existing router/VPN host before a new daemon starts.
        if ! command -v dockerd >/dev/null && [[ $(sysctl -n net.ipv4.ip_forward) == 1 ]] && iptables -S FORWARD | grep -q -- '-P FORWARD ACCEPT'; then
            python3 - <<'PY'
import json
from pathlib import Path
p = Path('/etc/docker/daemon.json')
p.parent.mkdir(parents=True, exist_ok=True)
data = json.loads(p.read_text()) if p.exists() else {}
data.setdefault('ip-forward-no-drop', True)
p.write_text(json.dumps(data, indent=2) + '\n')
PY
        fi
        install -m 0755 -d /etc/apt/keyrings
        curl --retry 3 -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
            "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
        apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update
        apt-get -o DPkg::Lock::Timeout=120 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
fi
systemctl enable --now docker nginx
if [[ -f $ROOT/state.json ]]; then
    mkdir -p "$ROOT/config-history"
    tar -czf "$ROOT/config-history/$(date -u +%Y%m%dT%H%M%SZ).tgz" -C "$ROOT" \
        state.json compose.yaml synapse/homeserver.yaml synapse/livekit-registration.yaml livekit.yaml
fi
python3 "$SOURCE/generate.py" --directory "$ROOT" --domain "$DOMAIN" --public-ip "$PUBLIC_IP"
install -m 700 "$SOURCE/manage.sh" "$ROOT/manage.sh"
install -m 700 "$SOURCE/create_admin.py" "$ROOT/create_admin.py"
install -m 700 "$SOURCE/registration.py" "$ROOT/registration.py"
mkdir -p /var/www/element-acme/.well-known/acme-challenge
chmod 755 /var/www/element-acme /var/www/element-acme/.well-known /var/www/element-acme/.well-known/acme-challenge
if [[ ! -f /etc/letsencrypt/live/matrix.$DOMAIN/fullchain.pem ]]; then
    install -m 644 "$ROOT/nginx-http.conf" /etc/nginx/sites-available/element-stack.conf
    ln -sfn /etc/nginx/sites-available/element-stack.conf /etc/nginx/sites-enabled/element-stack.conf
    nginx -t
    systemctl reload nginx
fi
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw allow 7881/tcp
    ufw allow 3478/udp
    ufw allow 50000:50200/udp
fi
CERT_ARGS=(--register-unsafely-without-email)
[[ -z $EMAIL ]] || CERT_ARGS=(--email "$EMAIL")
certbot certonly --webroot -w /var/www/element-acme --non-interactive --agree-tos \
    "${CERT_ARGS[@]}" --cert-name "matrix.$DOMAIN" \
    -d "matrix.$DOMAIN" -d "element.$DOMAIN" -d "rtc.$DOMAIN" -d "turn.$DOMAIN" --keep-until-expiring
mkdir -p "$ROOT/certs"
install -m 644 "/etc/letsencrypt/live/matrix.$DOMAIN/fullchain.pem" "$ROOT/certs/fullchain.pem"
install -m 600 "/etc/letsencrypt/live/matrix.$DOMAIN/privkey.pem" "$ROOT/certs/privkey.pem"
install -m 644 "$ROOT/nginx.conf" /etc/nginx/sites-available/element-stack.conf
ln -sfn /etc/nginx/sites-available/element-stack.conf /etc/nginx/sites-enabled/element-stack.conf
install -m 644 "$ROOT/nginx-stream.conf" /etc/nginx/modules-enabled/99-element-stack-stream.conf
nginx -t
systemctl reload nginx
cd "$ROOT"
docker compose config --quiet
docker compose pull
chown -R 991:991 "$ROOT/synapse"
docker compose up -d --wait --wait-timeout 240
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/element-stack <<EOF
#!/usr/bin/env bash
set -euo pipefail
[[ \${RENEWED_LINEAGE:-} == /etc/letsencrypt/live/matrix.$DOMAIN ]] || exit 0
install -m 644 "\$RENEWED_LINEAGE/fullchain.pem" "$ROOT/certs/fullchain.pem"
install -m 600 "\$RENEWED_LINEAGE/privkey.pem" "$ROOT/certs/privkey.pem"
nginx -t && systemctl reload nginx
cd "$ROOT"
docker compose restart livekit
EOF
chmod 700 /etc/letsencrypt/renewal-hooks/deploy/element-stack
systemctl enable --now certbot.timer
python3 "$ROOT/create_admin.py"
"$ROOT/manage.sh" health
docker compose images --format json > "$ROOT/installed-images.json"
echo "Installed. Web: https://element.$DOMAIN | Homeserver: https://matrix.$DOMAIN"
echo "Initial admin password: $ROOT/admin-credentials.txt (root-only; keep private)."
echo "Create more users: sudo $ROOT/manage.sh create-user"
echo 'Next: test voice and video between two real devices on different networks.'
