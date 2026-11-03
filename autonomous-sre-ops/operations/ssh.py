"""Pinned-host, fixed-operation SSH. Neither shell commands nor paths are tool inputs."""

from __future__ import annotations

import io
import json

from operations.lifecycle import TARGETS, normalized_target

ADDRESSES = {"linux": "10.42.0.10", "patch": "10.42.0.11", "backup": "10.42.0.12"}


class GuestError(RuntimeError):
    pass


def parse_host_trust(known: str) -> dict:
    import paramiko

    keys = {}
    for line in known.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] not in ADDRESSES.values() or fields[1] != "ssh-ed25519":
            raise ValueError("Host trust requires one authenticated Ed25519 key for each exact private address.")
        if fields[0] in keys:
            raise ValueError("Duplicate SSH host trust is forbidden.")
        try:
            entry = paramiko.hostkeys.HostKeyEntry.from_line(line)
        except paramiko.hostkeys.InvalidHostKey as error:
            raise ValueError("Invalid encoded SSH host key.") from error
        if entry is None or entry.key is None or entry.key.get_name() != "ssh-ed25519":
            raise ValueError("Invalid pinned SSH host key.")
        keys[fields[0]] = entry.key
    if set(keys) != set(ADDRESSES.values()):
        raise ValueError("Authenticated SSH host trust must contain all three targets.")
    return keys


class Guest:
    def __init__(self, vault_client) -> None:
        self.vault = vault_client

    def inspect(self, resource_id: str) -> dict:
        import paramiko

        role = TARGETS[normalized_target(resource_id)]
        private = self.vault.get_secret("runner-ssh-private-key").value
        known = self.vault.get_secret("runner-ssh-known-hosts").value
        if not private or not known:
            raise GuestError("Independent SSH credentials and host trust have not been bootstrapped.")
        key = paramiko.Ed25519Key.from_private_key(io.StringIO(private))
        client = paramiko.SSHClient()
        for hostname, host_key in parse_host_trust(known).items():
            client.get_host_keys().add(hostname, host_key.get_name(), host_key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                ADDRESSES[role], username="asrerunner", pkey=key, look_for_keys=False,
                allow_agent=False, timeout=10, auth_timeout=10, banner_timeout=10,
            )
            stdin, stdout, stderr = client.exec_command("asre-demo-control", timeout=30)
            stdin.write('{"operation":"inspect"}')
            stdin.channel.shutdown_write()
            raw = stdout.read(65537)
            error = stderr.read(8193)
            status = stdout.channel.recv_exit_status()
            if status != 0:
                raise GuestError(f"The fixed guest inspection failed with exit {status}; diagnostic bytes={len(error)}.")
            if len(raw) > 65536:
                raise ValueError("The guest response exceeded its independent size bound.")
            result = json.loads(raw)
            if not isinstance(result, dict) or "observed_at" not in result or "health" not in result:
                raise ValueError("The guest did not return the fixed inspection contract.")
            return result
        finally:
            client.close()
