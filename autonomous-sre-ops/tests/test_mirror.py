import io
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workloads" / "package-mirror"))
import mirror
from mirror import Handler, MirrorConfig, vendor_url


class MirrorTests(unittest.TestCase):
    def test_no_default_fault_and_no_metadata_failure(self):
        config = MirrorConfig.parse({"faults_enabled": False, "blocked_artifact": None, "expires_at": 0}, 100)
        self.assertFalse(config.blocks("/ubuntu/dists/jammy/InRelease", 100))
        with self.assertRaises(ValueError):
            MirrorConfig.parse({"faults_enabled": True, "blocked_artifact": "/ubuntu/dists/jammy/InRelease", "expires_at": 200}, 100)

    def test_only_exact_artifact_fails_and_expiry_restores_dependency(self):
        artifact = "/ubuntu/pool/main/f/fixture/fixture_1.0_amd64.deb"
        config = MirrorConfig.parse({"faults_enabled": True, "blocked_artifact": artifact, "expires_at": 200}, 100)
        self.assertTrue(config.blocks(artifact, 150))
        self.assertFalse(config.blocks(artifact.replace("1.0", "2.0"), 150))
        self.assertFalse(config.blocks("/ubuntu/dists/jammy/InRelease", 150))
        self.assertFalse(config.blocks(artifact, 200))

    def test_external_hosts_queries_and_traversal_are_rejected(self):
        for path in ("https://other.example/ubuntu/pool/a.deb", "//other.example/file", "/ubuntu/pool/../secret", "/ubuntu/pool/%2e%2e/secret", "/ubuntu/pool/a.deb?token=forbidden"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                vendor_url(path)

    def test_expiry_cannot_be_unbounded(self):
        for expiry in (float("inf"), 1901):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                MirrorConfig.parse({"faults_enabled": True, "blocked_artifact": "/ubuntu/pool/main/a.deb", "expires_at": expiry}, 100)

    def test_live_http_surface_preserves_metadata_and_fails_only_artifact(self):
        artifact = "/ubuntu/pool/main/f/fixture/fixture_1.0_amd64.deb"
        metadata = b"UNIT TEST VENDOR METADATA; NOT REAL PATCH EVIDENCE"
        upstream = MagicMock()
        response = upstream.open.return_value.__enter__.return_value
        response.status = 200
        response.headers = {"Content-Length": str(len(metadata)), "Content-Type": "text/plain"}
        response.read.side_effect = io.BytesIO(metadata).read
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"faults_enabled": True, "blocked_artifact": artifact, "expires_at": time.time() + 60}))
            server = HTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = f"http://127.0.0.1:{server.server_port}"
                with patch.object(mirror, "CONFIG_PATH", config), patch.object(mirror, "build_opener", return_value=upstream):
                    with urlopen(endpoint + "/livez", timeout=5) as result:
                        self.assertEqual(json.load(result)["status"], "alive")
                    with self.assertLogs("asre-package-mirror", level="WARNING"), self.assertRaises(HTTPError) as failed:
                        urlopen(endpoint + artifact, timeout=5)
                    self.assertEqual(failed.exception.code, 503)
                    with urlopen(endpoint + "/ubuntu/dists/jammy/InRelease", timeout=5) as result:
                        self.assertEqual(result.read(), metadata)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
