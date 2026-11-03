#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]]
file=/home/asrerunner/.ssh/authorized_keys
python3 - "$file" "$1" <<'PY'
import base64
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile

path = Path(sys.argv[1])
body = sys.argv[2]
ed25519_prefix = b"\0\0\0\x0bssh-ed25519\0\0\0\x20"


def decode_body(value):
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value):
        raise ValueError("Invalid public-key body")
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("Noncanonical public-key body")
    return decoded


decoded = decode_body(body)
if len(decoded) != len(ed25519_prefix) + 32 or not decoded.startswith(ed25519_prefix):
    raise ValueError("An Ed25519 public-key body is required")
identity = ("ssh-ed25519", body)
canonical = (
    f'restrict,command="sudo -n /usr/bin/python3 /usr/local/lib/asre-demo/guest_control.py --inspect-only" ssh-ed25519 {body}\n'
).encode("ascii")


def key_fields(row):
    text = row.decode("utf-8", errors="surrogateescape")
    if not text.strip() or text.lstrip().startswith("#"):
        return None
    # Read only the leading options/type/blob fields. Free-form comments may
    # contain quotes or key-looking text and must never be tokenized.
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    key_type = lexer.get_token()
    if not re.fullmatch(r"(?:ssh|ecdsa|sk)-[A-Za-z0-9@._+-]+", key_type):
        key_type = lexer.get_token()
    if not key_type or not re.fullmatch(r"(?:ssh|ecdsa|sk)-[A-Za-z0-9@._+-]+", key_type):
        raise ValueError("Ambiguous authorized_keys entry")
    key_body = lexer.get_token()
    if not key_body:
        raise ValueError("Missing public-key body")
    wire = decode_body(key_body)
    kind = key_type.encode("ascii")
    if not wire.startswith(len(kind).to_bytes(4, "big") + kind):
        raise ValueError("Public-key type/body mismatch")
    if key_type == "ssh-ed25519" and (
        len(wire) != len(ed25519_prefix) + 32 or not wire.startswith(ed25519_prefix)
    ):
        raise ValueError("Invalid Ed25519 entry")
    return key_type, key_body


def check_metadata(info):
    if not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o644):
        raise ValueError("authorized_keys must be a regular root:root:644 file")


def read_destination():
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        check_metadata(os.fstat(stream.fileno()))
        return stream.read()


original = read_destination()
rows = original.splitlines(keepends=True)
# Prepending also preserves an unrelated final line without a newline.
proposed = canonical + b"".join(row for row in rows if key_fields(row) != identity)
if sum(key_fields(row) == identity for row in proposed.splitlines(keepends=True)) != 1:
    raise ValueError("Runtime authorization must be unique")

temporary = None
try:
    fd, temporary = tempfile.mkstemp(prefix=".authorized_keys.", dir=path.parent)
    with os.fdopen(fd, "w+b") as stream:
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), 0o644)
        stream.write(proposed)
        stream.flush()
        os.fsync(stream.fileno())
        check_metadata(os.fstat(stream.fileno()))
        stream.seek(0)
        if stream.read() != proposed:
            raise ValueError("Staged authorization verification failed")
    # Enrollment is serialized by the caller; no distributed writer protocol.
    os.replace(temporary, path)
    temporary = None
    if read_destination() != proposed:
        raise ValueError("Installed authorization verification failed")
finally:
    if temporary is not None:
        os.unlink(temporary)
print("ASRE_RUNTIME_SSH_INSTALLED")
PY
