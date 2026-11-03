"""Unmodified Ubuntu vendor proxy with one exact, independently expiring artifact fault."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LOG = logging.getLogger("asre-package-mirror")
CONFIG_PATH = Path(os.environ.get("MIRROR_CONFIG", "/app/mirror-config.json"))
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


def vendor_url(raw_path: str) -> str:
    parts = urlsplit(raw_path)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise ValueError("Only local repository paths without query/fragment are accepted.")
    path = unquote(parts.path, errors="strict")
    if not re.fullmatch(r"/ubuntu/(dists|pool)/[A-Za-z0-9._+~:/-]+", path):
        raise ValueError("The path is outside the vendor repository allowlist.")
    if any(part in {"", ".", ".."} for part in path.split("/")[1:]):
        raise ValueError("Empty or traversal path segments are not accepted.")
    return "https://archive.ubuntu.com" + quote(path, safe="/+._-~")


@dataclass(frozen=True)
class MirrorConfig:
    faults_enabled: bool
    blocked_artifact: str | None
    expires_at: float

    @classmethod
    def parse(cls, value: dict, now: float) -> MirrorConfig:
        if not isinstance(value, dict) or set(value) != {"faults_enabled", "blocked_artifact", "expires_at"}:
            raise ValueError("Unexpected mirror configuration.")
        result = cls(**value)
        if type(result.faults_enabled) is not bool or type(result.expires_at) not in {float, int}:
            raise ValueError("Invalid mirror fault settings.")
        if not math.isfinite(result.expires_at):
            raise ValueError("Mirror expiry must be finite.")
        if result.faults_enabled:
            if not isinstance(result.blocked_artifact, str):
                raise ValueError("An approved exact vendor package artifact is required.")
            vendor_url(result.blocked_artifact)
            if (
                not result.blocked_artifact.startswith("/ubuntu/pool/")
                or not result.blocked_artifact.endswith(".deb")
                or not 0 < result.expires_at <= now + 1800
            ):
                raise ValueError("Only one package artifact may fail for at most 30 minutes; metadata must remain reachable.")
        elif result.blocked_artifact is not None or result.expires_at != 0:
            raise ValueError("Inactive mirror settings cannot retain a latent artifact fault.")
        return result

    def blocks(self, path: str, now: float) -> bool:
        return self.faults_enabled and now < self.expires_at and vendor_url(path) == vendor_url(self.blocked_artifact)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise HTTPError(request.full_url, 502, "Vendor redirect rejected; validate it before changing the allowlist.", headers, file)


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.proxy(head=True)

    def do_GET(self) -> None:
        self.proxy(head=False)

    def proxy(self, head: bool) -> None:
        headers_sent = False
        if self.path == "/livez":
            body = b'{"status":"alive"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            headers_sent = True
            if not head:
                self.wfile.write(body)
            return
        try:
            with CONFIG_PATH.open(encoding="utf-8") as stream:
                config = MirrorConfig.parse(json.load(stream), time.time())
            url = vendor_url(self.path)
            if config.blocks(self.path, time.time()):
                LOG.warning("Approved package artifact unavailable path=%s", self.path)
                self.send_error(503, "Approved demo package artifact temporarily unavailable")
                return
            request = Request(url, method="HEAD" if head else "GET", headers={"User-Agent": "asre-demo/1.0", "Accept-Encoding": "identity"})
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw_length = response.headers.get("Content-Length")
                length = int(raw_length) if raw_length is not None else None
                if length is not None and not 0 <= length <= MAX_ARTIFACT_BYTES:
                    raise ValueError("Vendor object exceeds the bounded transfer size.")
                self.send_response(response.status)
                if length is not None:
                    self.send_header("Content-Length", str(length))
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
                self.end_headers()
                transferred = 0
                if not head:
                    while block := response.read(64 * 1024):
                        transferred += len(block)
                        if transferred > MAX_ARTIFACT_BYTES:
                            raise OSError("Vendor transfer exceeded the independent byte cap.")
                        self.wfile.write(block)
        except HTTPError as error:
            LOG.error("Vendor request failed status=%s path=%s", error.code, self.path)
            self.send_error(error.code, "Vendor dependency request failed")
        except (OSError, URLError, ValueError, TypeError) as error:
            LOG.error("Mirror request failed path=%s error=%s", self.path, error)
            if headers_sent:
                self.close_connection = True
            else:
                self.send_error(502, "Mirror dependency/configuration failure")

    def log_message(self, format: str, *args: object) -> None:
        LOG.info(format, *args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
