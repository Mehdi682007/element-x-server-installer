#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/opt/element-stack
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
if [[ $# -eq 0 || ${1:-} == menu ]]; then
    exec bash "$(dirname -- "${BASH_SOURCE[0]}")/menu.sh"
fi
cd "$ROOT"
case ${1:-help} in
    status) docker compose ps ;;
    logs) docker compose logs --tail 100 "${@:2}" ;;
    restart) docker compose restart "${@:2}" ;;
    create-user) docker compose exec synapse register_new_matrix_user -c /data/homeserver.yaml http://localhost:8008 ;;
    registration)
        exec 9>/run/element-stack-install.lock
        flock -n 9 || { echo 'An installation or registration change is already running.'; exit 1; }
        python3 "$ROOT/registration.py" "${2:-status}"
        ;;
    health|diagnostics|fix|backup|restore|purge|upload-limit|update-element)
        python3 "$ROOT/manager.py" "$@" ;;
    users) shift; python3 "$ROOT/accounts.py" "$@" ;;
    *) echo "Usage: $0 {menu|status|logs|restart|create-user|users|registration|health|diagnostics|fix|backup|restore|purge|upload-limit|update-element}" ;;
esac
