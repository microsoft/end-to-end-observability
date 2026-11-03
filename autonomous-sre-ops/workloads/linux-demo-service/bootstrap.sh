#!/usr/bin/env bash
set -euo pipefail

role="${1:?A target role is required}"
case "$role" in linux|patch|backup) ;; *) echo "Unknown target role" >&2; exit 1 ;; esac

getent group asredemo >/dev/null || groupadd --system asredemo
id asredemo >/dev/null 2>&1 || useradd --system --gid asredemo --no-create-home --shell /usr/sbin/nologin asredemo
id asrerunner >/dev/null 2>&1 || useradd --system --home-dir /home/asrerunner --shell /bin/sh asrerunner
install -d -m 0755 -o root -g root /home/asrerunner /home/asrerunner/.ssh
printf 'restrict,command="sudo -n /usr/bin/python3 /usr/local/lib/asre-demo/guest_control.py --inspect-only" %s\n' "$(cat /etc/asre-demo/runner.pub)" > /home/asrerunner/.ssh/authorized_keys
chown root:root /home/asrerunner/.ssh/authorized_keys
chmod 0644 /home/asrerunner/.ssh/authorized_keys
install -d -m 0755 -o root -g root /var/lib/asre-demo /srv/asre-demo

if [[ "$role" == "linux" ]]; then
    device=/dev/disk/azure/scsi1/lun0
    for ((attempt=0; attempt<120; attempt++)); do
        [[ -b "$device" ]] && break
        sleep 5
    done
    [[ -b "$device" ]] || { echo "The dedicated LUN 0 data disk did not attach" >&2; exit 1; }
    root_device="$(findmnt -n -o SOURCE /)"
    [[ "$(readlink -f "$device")" != "$(readlink -f "$root_device")" ]] || { echo "Refusing the OS device" >&2; exit 1; }
    if ! blkid "$device" >/dev/null 2>&1; then
        [[ "$(blockdev --getsize64 "$device")" == "4294967296" ]] || { echo "Unexpected disposable disk size" >&2; exit 1; }
        mkfs.ext4 -L ASREDEMO "$device"
    fi
    [[ "$(blkid -s LABEL -o value "$device")" == "ASREDEMO" ]] || { echo "Existing disk is not owned by this demo" >&2; exit 1; }
    if ! grep -q '^LABEL=ASREDEMO ' /etc/fstab; then
        printf 'LABEL=ASREDEMO /srv/asre-demo ext4 defaults,nofail 0 2\n' >> /etc/fstab
    fi
    mountpoint -q /srv/asre-demo || mount /srv/asre-demo
    [[ "$(stat -c %d /)" != "$(stat -c %d /srv/asre-demo)" ]] || { echo "Disposable disk is not isolated" >&2; exit 1; }
    printf 'asre-demo\n' > /srv/asre-demo/.asre-demo-owner
    chmod 0644 /srv/asre-demo/.asre-demo-owner
fi

install -d -m 0700 -o asredemo -g asredemo /srv/asre-demo/data
getent passwd asrerunner | grep -q ':/home/asrerunner:/bin/sh$'
PYTHONPATH=/usr/local/lib/asre-demo /usr/bin/python3 -c 'from demo_common import Config; Config().write()'
chown root:asredemo /var/lib/asre-demo/config.json
chmod 0640 /var/lib/asre-demo/config.json
visudo -cf /etc/sudoers.d/asre-demo
systemctl daemon-reload
systemctl enable --now asre-demo.service
systemctl is-active --quiet asre-demo.service
