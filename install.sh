#!/usr/bin/env bash
set -Eeuo pipefail
# Support a local checkout and the original bash <(curl ...) entry point.
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -f $SOURCE/menu.sh || ! -f $SOURCE/setup.sh ]]; then
    [[ $EUID -eq 0 ]] || { echo 'Please run as ROOT (sudo -i).'; exit 1; }
    command -v curl >/dev/null || { echo 'Install curl first: apt-get install -y curl'; exit 1; }
    command -v python3 >/dev/null || { echo 'Install python3 first: apt-get install -y python3'; exit 1; }
    SOURCE=$(mktemp -d /var/tmp/pd-element-x.XXXXXXXX)
    chmod 700 "$SOURCE"
    REPO=https://api.github.com/repos/Mehdi682007/element-x-server-installer
    COMMIT=$(curl --retry 3 -fsSL "$REPO/commits/main" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
    [[ $COMMIT =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid repository revision'; exit 1; }
    for file in install.sh setup.sh menu.sh manage.sh generate.py create_admin.py registration.py manager.py accounts.py detect_ip.py; do
        curl --retry 3 -fsSL "https://raw.githubusercontent.com/Mehdi682007/element-x-server-installer/$COMMIT/$file" -o "$SOURCE/$file"
        chmod 700 "$SOURCE/$file"
    done
    echo "Downloaded PD-Element X revision: $COMMIT"
fi
if [[ ${1:-} == --install || ( -n ${DOMAIN:-} && -n ${PUBLIC_IP:-} ) ]]; then
    [[ ${1:-} != --install ]] || shift
    exec bash "$SOURCE/setup.sh" "$@"
fi
exec bash "$SOURCE/menu.sh" "$@"
