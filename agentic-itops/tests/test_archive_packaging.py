import json

import pytest

from deployment.package import add_artifacts


def test_archive_artifacts_are_explicit_bounded_and_credential_free(tmp_path):
    path = tmp_path / "archive.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    files = {}
    add_artifacts(files, [f"runtime-archive-1.json={path}"])
    assert files == {".runtime/runtime-archive-1.json": path.read_bytes()}
    for name in ("runtime-archive-0.json", "runtime-archive-9.json",
                 "../runtime-archive-1.json", "other.json"):
        with pytest.raises(ValueError, match="approved runtime filename"):
            add_artifacts({}, [f"{name}={path}"])
    path.write_text(json.dumps({"password": "not-a-real-secret"}), encoding="utf-8")
    with pytest.raises(ValueError, match="credentials"):
        add_artifacts({}, [f"runtime-archive-1.json={path}"])
