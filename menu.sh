#!/usr/bin/env bash
set -uo pipefail
umask 077
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=/opt/element-stack
LOG_FILE=/var/log/element_stack_manager.log
VERSION=2.0
ASCII_BANNER=$(cat <<'BANNER'
╔════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                ║
║   _______  ______              _______  _        _______  _______  _______  _       _________  ║
║  (  ____ )(  __  \            (  ____ \( \      (  ____ \(       )(  ____ \( (    /|\__   __/  ║
║  | (    )|| (  \  )           | (    \/| (      | (    \/| () () || (    \/|  \  ( |   ) (     ║
║  | (____)|| |   ) |   _____   | (__    | |      | (__    | || || || (__    |   \ | |   | |     ║
║  |  _____)| |   | |  (_____)  |  __)   | |      |  __)   | |(_)| ||  __)   | (\ \) |   | |     ║
║  | (      | |   ) |           | (      | |      | (      | |   | || (      | | \   |   | |     ║
║  | )      | (__/  )           | (____/\| (____/\| (____/\| )   ( || (____/\| )  \  |   | |     ║
║  |/       (______/            (_______/(_______/(_______/|/     \|(_______/|/    )_)   )_(     ║
║                                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════════════════════╝


BANNER
)

print_header() {
    [[ ${1:-} == preview ]] || clear || true
    echo "$ASCII_BANNER"
    echo "🚀 PD-Element X — Matrix Stack Manager v${VERSION}"
    echo "🔗 Telegram: https://t.me/MYoutub"
    echo "📺 YouTube : https://www.youtube.com/@ParsDigital"
    echo "📝 Log file: ${LOG_FILE}"
    echo
}

print_menu() {
    echo "====== Matrix Stack Manager ======"
    echo "1)  🧩 Install / Reinstall Matrix + Element X + LiveKit + TURN"
    echo "2)  👑 Create admin user (interactive)"
    echo "3)  👤 Create normal user (interactive)"
    echo "4)  🎲 Create user with RANDOM password (auto)"
    echo "5)  ♻️ Reactivate user (set new password)"
    echo "6)  📋 List users"
    echo "7)  🚫 Deactivate user (safe)"
    echo "8)  📦 Set upload limits (Nginx + Synapse)"
    echo "9)  🧾 Toggle registration ON/OFF"
    echo "10) 🔎 Health Check"
    echo "11) 🧰 Fix Wizard (auto-fix common issues)"
    echo "12) 💾 Backup server"
    echo "13) ♻️ Restore backup"
    echo "14) 📞 Call Diagnostics (MatrixRTC / LiveKit / TURN)"
    echo "15) ⬆️  Update Element Web"
    echo "16) 🧨 Full uninstall / purge"
    echo "17) 🔑 Administrator login / password / new username"
    echo "18) 🚪 Exit"
    echo "=================================="
}

installed() {
    [[ -f $ROOT/state.json && -f $ROOT/manager.py ]] || {
        echo "⚠️  Element X stack not installed. Run option 1 first."; return 1;
    }
}

install_stack() {
    print_header
    echo "🧩 === Matrix + Element X + LiveKit + TURN Installer (Install Mode) ==="
    local domain ip email answer engine
    engine="$SOURCE/setup.sh"
    [[ -f $engine ]] || engine=/opt/element-x-installer/setup.sh
    if [[ -f $ROOT/state.json ]]; then
        domain=$(python3 -c 'import json; print(json.load(open("/opt/element-stack/state.json"))["domain"])') || return
        ip=$(python3 -c 'import json; print(json.load(open("/opt/element-stack/state.json"))["public_ip"])') || return
        echo "Existing installation found; domain, IP and secrets will be preserved."
    else
        read -rp "🏠 Enter base domain (e.g. example.com): " domain || return
        ip=$(python3 "$(dirname -- "$engine")/detect_ip.py") || return
    fi
    read -rp "✉️  Let's Encrypt email (optional): " email || return
    echo "===== INSTALL CONFIGURATION SUMMARY ====="
    echo "Matrix: https://matrix.$domain"
    echo "Web:    https://element.$domain"
    echo "RTC:    https://rtc.$domain"
    echo "TURN:   turn.$domain"
    echo "Public IPv4: $ip"
    echo "All four A records must point to this IPv4 (DNS only)."
    echo "Database: PostgreSQL; existing native PD-Element is not auto-migrated."
    echo "========================================="
    read -rp "✅ Continue with installation? (y/n): " answer || return
    [[ $answer == y || $answer == Y ]] || { echo 'Cancelled.'; return 0; }
    env DOMAIN="$domain" PUBLIC_IP="$ip" EMAIL="$email" EMAIL_PROVIDED=1 bash "$engine"
}

user_action() {
    installed || return
    print_header
    echo "👥 === User Management ==="
    python3 "$ROOT/accounts.py" "$@"
}

random_user() {
    local role
    echo '1) Normal user'
    echo '2) Admin user'
    read -rp 'Choose [1-2]: ' role || return
    case $role in
        1) user_action create --random-password ;;
        2) user_action create --random-password --admin ;;
        *) echo 'Invalid option.'; return 1 ;;
    esac
}

reactivate() {
    local role
    echo '1) Reactivate as NORMAL user'
    echo '2) Reactivate as ADMIN user'
    echo '3) Back'
    read -rp 'Choose [1-3]: ' role || return
    case $role in
        1) user_action reactivate ;;
        2) user_action reactivate --admin ;;
        3) return 0 ;;
        *) echo 'Invalid option.'; return 1 ;;
    esac
}

toggle_registration() {
    installed || return
    print_header
    echo '🧾 === Toggle Registration (ON/OFF) ==='
    bash "$ROOT/manage.sh" registration status || return
    echo '1) Turn ON registration (anyone can sign up in Element Web)'
    echo '2) Turn OFF registration (admin-created accounts only)'
    echo '3) Back'
    local choice
    read -rp 'Choose [1-3]: ' choice || return
    case $choice in
        1) bash "$ROOT/manage.sh" registration open ;;
        2) bash "$ROOT/manage.sh" registration close ;;
        3) return 0 ;;
        *) echo 'Invalid option.'; return 1 ;;
    esac
}

operation() {
    installed || return
    print_header
    python3 "$ROOT/manager.py" "$@"
}

backup_server() {
    local choice
    echo 'Include the TLS certificate/key copy in the backup?'
    echo '1) Yes'
    echo '2) No (restore uses the current host certificate)'
    read -rp 'Choose [1-2]: ' choice || return
    case $choice in
        1) operation backup ;;
        2) operation backup --without-tls ;;
        *) echo 'Invalid option.'; return 1 ;;
    esac
}

update_element() {
    local choice version
    echo '1) Enter version manually (recommended)'
    echo '2) Use latest (GitHub API)'
    echo '3) Back'
    read -rp 'Choose [1-3]: ' choice || return
    case $choice in
        1) read -rp 'Enter Element Web version (e.g. 1.12.32): ' version || return
           operation update-element "$version" ;;
        2) operation update-element latest ;;
        3) return 0 ;;
        *) echo 'Invalid option.'; return 1 ;;
    esac
}

dispatch() {
    case $1 in
        1) install_stack ;;
        2) user_action create --admin ;;
        3) user_action create ;;
        4) random_user ;;
        5) reactivate ;;
        6) user_action list ;;
        7) user_action deactivate ;;
        8) operation upload-limit ;;
        9) toggle_registration ;;
        10) operation health ;;
        11) operation fix ;;
        12) backup_server ;;
        13) operation restore ;;
        14) operation diagnostics ;;
        15) update_element ;;
        16) operation purge ;;
        17) user_action admin-settings ;;
        *) echo 'Invalid option.'; return 1 ;;
    esac
}

if [[ ${1:-} == --preview ]]; then
    print_header preview
    print_menu
    exit 0
fi
[[ $EUID -eq 0 ]] || { echo '❌ Please run this script as ROOT (sudo -i).'; exit 1; }
# Log action numbers/results only. Never tee passwords or tokens into a log file.
touch "$LOG_FILE" && chmod 600 "$LOG_FILE"
while true; do
    print_header
    print_menu
    read -rp 'Choose an option [1-18]: ' choice || exit 0
    [[ $choice != 18 ]] || { echo 'Bye.'; exit 0; }
    if [[ ! $choice =~ ^([1-9]|1[0-7])$ ]]; then
        echo 'Invalid option.'
    elif dispatch "$choice"; then
        printf '%s option=%s result=ok\n' "$(date -u +%FT%TZ)" "$choice" >> "$LOG_FILE"
    else
        echo '❌ Operation failed. Read the error above; no success was recorded.'
        printf '%s option=%s result=failed\n' "$(date -u +%FT%TZ)" "$choice" >> "$LOG_FILE"
    fi
    read -rp 'Press Enter to continue...' answer || exit 0
done
