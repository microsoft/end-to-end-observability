#!/bin/sh
set -eu

storage="${1:?Provide the deployed operations storage account name}"
vault="${2:?Provide the deployed Key Vault name}"
timeout 720 cloud-init status --wait
systemctl is-enabled --quiet asre-demo.service
systemctl is-active --quiet asre-demo.service
visudo -cf /etc/sudoers.d/asre-demo

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/asre-demo python3 - "$storage" "$vault" <<'PY'
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import subprocess
import sys
from urllib.request import urlopen

from demo_common import Config, validate_data_root


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


policy_path = Path("/etc/asre-demo/policy.json")
policy = json.loads(policy_path.read_text())
require(policy["owner"] == "asre-demo", "Unexpected guest policy owner")
require(policy["role"] in {"linux", "patch", "backup"}, "Unexpected target role")
require(policy["faults_enabled"] is False, "Fault injection must remain disabled")
require(socket.gethostname() == f'vm-asre-{policy["role"]}-cus', "Policy/host mismatch")
require(policy_path.stat().st_uid == 0 and stat.S_IMODE(policy_path.stat().st_mode) == 0o600,
        "Guest policy permissions differ from the root-only baseline")
require(Config.read().mode == "normal", "Workload configuration is not normal")
with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
    health = json.loads(response.read(4096))
require(health["status"] == "ok" and health["mode"] == "normal", "Workload is unhealthy")
require(health["scenario_id"] is None and health["error"] is None
        and health["retained_bytes"] == 0 and health["written_bytes"] == 0,
        "Workload retains an active or failed scenario")

for name, expected in {"User": "asredemo", "MemoryMax": "134217728",
                       "MemorySwapMax": "0", "NoNewPrivileges": "yes"}.items():
    actual = subprocess.run(["systemctl", "show", "asre-demo.service", f"--property={name}",
                             "--value"], check=True, capture_output=True, text=True).stdout.strip()
    require(actual == expected, f"Unexpected workload service setting: {name}={actual}")

authorized_keys = Path("/home/asrerunner/.ssh/authorized_keys")
require(authorized_keys.read_text().startswith(
    'restrict,command="sudo -n /usr/bin/python3 /usr/local/lib/asre-demo/guest_control.py --inspect-only" ssh-'),
    "Runner SSH key is not restricted to the fixed helper")
require(authorized_keys.stat().st_uid == 0 and not authorized_keys.stat().st_mode & 0o022,
        "Runner authorized keys are writable by a non-root identity")
if policy["role"] == "linux":
    validate_data_root()
    device = "/dev/disk/azure/scsi1/lun0"
    source = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/srv/asre-demo"],
                            check=True, capture_output=True, text=True).stdout.strip()
    require(os.stat(device).st_rdev == os.stat(source).st_rdev,
            "Incident filesystem is not the dedicated LUN 0 disk")

network = ipaddress.ip_network("10.42.2.0/27")
private_services = {}
for host in [f"{sys.argv[1]}.{service}.core.windows.net" for service in ("blob", "table", "queue")] + [
        f"{sys.argv[2]}.vault.azure.net"]:
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(
        host, 443, socket.AF_INET, socket.SOCK_STREAM)})
    require(addresses and all(ipaddress.ip_address(address) in network for address in addresses),
            f"Service did not resolve to the lab private endpoint subnet: {host}")
    with socket.create_connection((host, 443), timeout=5) as connection:
        with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
            pass
    private_services[host] = addresses

print("ASRE_BASELINE_VERIFIED " + json.dumps({
    "role": policy["role"], "faults_enabled": False, "workload": health,
    "incident_disk_isolated": policy["role"] == "linux",
    "private_dns_tls": private_services,
}, sort_keys=True))
PY
