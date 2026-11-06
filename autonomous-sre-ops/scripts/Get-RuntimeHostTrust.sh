#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in 10.42.0.10|10.42.0.11|10.42.0.12) ;; *) exit 2 ;; esac
systemctl is-active --quiet ssh.service
test "$(stat -c '%U:%G:%a' /etc/ssh/ssh_host_ed25519_key.pub)" = 'root:root:644'
read -r algorithm key _ < /etc/ssh/ssh_host_ed25519_key.pub
test "$algorithm" = ssh-ed25519
printf 'ASRE_SSH_HOST_KEY %s %s %s\n' "$1" "$algorithm" "$key"
