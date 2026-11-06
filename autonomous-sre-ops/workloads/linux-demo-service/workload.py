"""A real disk-growth and cgroup-bounded memory workload; no fault-control HTTP API."""

from __future__ import annotations

import json
import logging
import logging.handlers
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from demo_common import CHUNK_BYTES, CONFIG_PATH, DATA_ROOT, OWNER, Config, atomic_json, validate_data_root

LOG = logging.getLogger("asre-demo")


class Workload:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.failure: str | None = None
        self.mode = "starting"
        self.retained: list[bytearray] = []
        self.written = 0
        self.scenario_id: str | None = None

    def tick(self, now: float) -> float:
        config = Config.read(CONFIG_PATH)
        if config.mode != "normal" and now >= config.expires_at:
            self.retained.clear()
            self.mode = "expired"
            LOG.warning("asre-demo local expiry scenario=%s; root watchdog owns cleanup", config.scenario_id)
            return 2
        if config.scenario_id != self.scenario_id:
            self.retained.clear()
            self.written = 0
            self.scenario_id = config.scenario_id
        self.mode = config.mode
        if config.mode == "normal":
            self.retained.clear()
            return 2
        if config.mode in {"memory", "memory-oom"}:
            retained = sum(map(len, self.retained))
            size = min(CHUNK_BYTES, config.byte_limit - retained)
            if size > 0:
                block = bytearray(size)
                for index in range(0, size, 4096):
                    block[index] = 1
                self.retained.append(block)
            LOG.info("asre-demo sample scenario=%s mode=%s retained_bytes=%d", self.scenario_id, self.mode, sum(map(len, self.retained)))
            return config.interval_seconds

        validate_data_root()
        directory = DATA_ROOT / "data" / config.scenario_id
        if not directory.exists():
            directory.mkdir(mode=0o700)
            names = [f"{index:08d}.bin" for index in range(math.ceil(config.byte_limit / CHUNK_BYTES))]
            atomic_json(directory / "manifest.json", {"owner": OWNER, "scenario_id": config.scenario_id, "files": names}, 0o600)
        if directory.is_symlink() or directory.resolve().parent != (DATA_ROOT / "data").resolve():
            raise ValueError("Unexpected scenario directory.")
        usage = os.statvfs(DATA_ROOT)
        used_fraction = 1 - usage.f_bavail / usage.f_blocks
        if used_fraction >= 0.85 or self.written >= config.byte_limit:
            LOG.warning("asre-demo disk cap scenario=%s written_bytes=%d used_fraction=%.4f", self.scenario_id, self.written, used_fraction)
            return 10
        available_budget = int((0.85 * usage.f_blocks - (usage.f_blocks - usage.f_bavail)) * usage.f_frsize)
        size = min(CHUNK_BYTES, config.byte_limit - self.written, available_budget)
        if size <= 0:
            return 10
        name = f"{self.written // CHUNK_BYTES:08d}.bin"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(directory / name, flags, 0o600)
        with os.fdopen(fd, "wb", buffering=0) as stream:
            block = bytes(min(size, 1024 * 1024))
            remaining = size
            while remaining:
                written = stream.write(block[:remaining])
                if not written:
                    raise OSError("The disposable filesystem accepted no data.")
                remaining -= written
            os.fsync(stream.fileno())
        self.written += size
        LOG.info("asre-demo sample scenario=%s mode=disk written_bytes=%d", self.scenario_id, self.written)
        return config.interval_seconds

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                self.stop.wait(self.tick(time.time()))
        except (OSError, ValueError, TypeError) as error:
            self.failure = str(error)
            LOG.exception("asre-demo workload failed")
            self.stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    LOG.addHandler(logging.handlers.SysLogHandler(address="/dev/log"))
    workload = Workload()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            body = json.dumps({
                "status": "failed" if workload.failure else "ok",
                "mode": workload.mode,
                "scenario_id": workload.scenario_id,
                "retained_bytes": sum(map(len, workload.retained)),
                "written_bytes": workload.written,
                "error": workload.failure,
            }).encode()
            self.send_response(503 if workload.failure or workload.mode == "starting" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            LOG.debug(format, *args)

    thread = threading.Thread(target=workload.run, name="bounded-workload", daemon=True)
    thread.start()
    try:
        ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
    finally:
        workload.stop.set()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
