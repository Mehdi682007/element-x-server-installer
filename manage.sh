#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/opt/element-stack
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
cd "$ROOT"
DOMAIN=$(python3 -c 'import json; print(json.load(open("state.json"))["domain"])')
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
    health)
        docker compose ps
        curl -fsS --max-time 20 "https://matrix.$DOMAIN/_matrix/client/versions" >/dev/null
        curl -fsS --max-time 20 "https://matrix.$DOMAIN/.well-known/matrix/client" >/dev/null
        curl -fsS --max-time 20 "https://matrix.$DOMAIN/.well-known/matrix/server" >/dev/null
        curl -fsS --max-time 20 "https://rtc.$DOMAIN/livekit/jwt/healthz" >/dev/null
        curl -fsS --max-time 20 "https://element.$DOMAIN/config.json" >/dev/null
        curl -fsS --max-time 10 http://127.0.0.1:7880/ >/dev/null
        echo 'HTTPS, discovery, Synapse, Element, JWT and LiveKit probes passed.'
        echo 'This is not a substitute for an end-to-end audio/video test.'
        ;;
    backup)
        BACKUPS=/var/backups/element-stack
        mkdir -p "$BACKUPS"
        chmod 700 "$BACKUPS"
        JOB=$(mktemp -d "$BACKUPS/.backup.XXXXXXXX")
        ARCHIVE="$BACKUPS/element-stack-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
        echo 'Pausing Synapse briefly to keep database and media backup consistent...'
        trap 'docker compose start synapse >/dev/null' EXIT
        docker compose stop synapse
        docker compose exec -T postgres pg_dump -U synapse -Fc synapse > "$JOB/database.dump"
        tar --exclude='./postgres' --exclude='./config-history' -czf "$JOB/files.tar.gz" -C "$ROOT" .
        tar -czf "$ARCHIVE" -C "$JOB" database.dump files.tar.gz
        rm -f -- "$JOB/database.dump" "$JOB/files.tar.gz"
        rmdir -- "$JOB"
        docker compose start synapse
        trap - EXIT
        echo "Private backup: $ARCHIVE — copy off-server. Old backups are not deleted automatically."
        ;;
    *) echo "Usage: $0 {status|logs [service]|restart [service]|create-user|registration [open|close|status]|health|backup}" ;;
esac
