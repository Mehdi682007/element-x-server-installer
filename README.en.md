# PD-Element X

[فارسی](README.md) | **English**

Host your own messaging server with Element X support, a web client, and voice/video calls. Includes Synapse, PostgreSQL, Element Web, LiveKit, MatrixRTC, and automatic HTTPS.

Element X is a mobile app; this project installs its server infrastructure.

## Requirements

- Ubuntu 22.04 or 24.04, amd64 or arm64, with root access. Installation tested on Ubuntu 22.04 amd64.
- A domain and static public IPv4 address. For light use, start with 2 CPU cores, 4 GB RAM, and 30 GB disk space.
- At least 8 GB free disk space and access to Ubuntu, Docker, and GHCR repositories.
- Prefer a fresh server with port 443 available. Existing web services may require manual configuration.

## DNS and ports

| Type | Name | Value |
|---|---|---|
| A | matrix | Server IP |
| A | element | Server IP |
| A | rtc | Server IP |
| A | turn | Server IP |

Use **DNS only** in Cloudflare. Do not add AAAA records for these four names. Your root domain can remain unchanged.

Allow these inbound ports in your server and VPS provider firewalls:

| Protocol | Ports |
|---|---|
| TCP | Your SSH port, 80, 443, 7881 |
| UDP | 3478, 50000–50200 |

Do not publicly expose internal service or database ports. The installer adds rules to an active UFW firewall; it does not enable an inactive firewall.

## Install

Connect over SSH as root and run:

```bash
apt-get update
apt-get install -y curl python3
bash <(curl -fsSL https://raw.githubusercontent.com/Mehdi682007/element-x-server-installer/main/install.sh)
```

Choose option **1** and enter your domain and optional email. The public IPv4 address is detected automatically; manual input is requested only if detection fails. Existing installations keep their saved IP. To specify an IP for a new installation, run `sudo env PUBLIC_IP=203.0.113.10 pd-element-x` with your own address. To reopen the menu:

```bash
sudo pd-element-x
```

## Accounts and sign-in

- Option **3** creates a regular user; **2** creates an administrator; **4** generates a random password.
- Initial admin credentials are saved in `/opt/element-stack/admin-credentials.txt`. Keep this file private.
- Option **18** shows saved admin credentials, changes an admin password, or creates a new primary admin username. Password changes require an admin login; if forgotten, create a new admin through this option. Matrix usernames cannot be renamed: the new account does not inherit history or memberships, and the old account is retained. Passwords changed outside this tool may differ from the saved value.
- In Element X, set your server to `https://matrix.example.com`.
- Web client: `https://element.example.com`. Replace `example.com` with your domain.

Public registration is disabled by default. Option **9** enables or disables it; administrators can create accounts in either mode. When enabled, users sign up at `https://element.example.com/#/register`. Email verification and CAPTCHA are not configured. Changing registration briefly restarts Synapse; existing accounts remain available.

An admin account does not add a management panel to Element. Use the SSH menu to manage the server. Save your account's Recovery Key separately.

## Management and maintenance

| Option | Action |
|---|---|
| 5–7 | Reactivate inactive accounts, list, and deactivate users |
| 8 | Set upload limits |
| 10–11 | Check health and fix common issues |
| 12–13 | Back up and restore |
| 14 | Diagnose calls |
| 15 | Update Element Web; update the mobile app through its app store |
| 16 | Uninstall after confirmation; backups are retained |

Backups are stored in `/var/backups/element-stack`. Copy them off-server and monitor disk space. Backups temporarily stop Synapse. Restore requires an existing installation with the same domain/IP and PostgreSQL major version. Only restore trusted backups. The previous runtime directory is retained after restore and consumes disk space.

Back up before updating or reinstalling. Reinstalling rebuilds generated configuration and does not change the installation's domain/IP. Migration from other installations and disk expansion are not automatic.

Test calls with two accounts on two devices using different networks. A health check does not replace a voice/video test. Certificates renew automatically; restarting the call service to load a renewed certificate may briefly interrupt active calls.

An independent community project, not an official Element product. [MIT license](LICENSE)
