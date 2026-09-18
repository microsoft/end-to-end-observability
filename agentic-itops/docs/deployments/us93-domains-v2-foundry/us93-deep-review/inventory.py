import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEST = Path(__file__).resolve().parent


def owner(path):
    first = path.parts[0]
    if first in {"agents", "docs"} or str(path) in {"README.md", r"scripts\emit_agent_manifests.py"}:
        return "agents-docs"
    if first in {"data", "evaluations", "tests"} or str(path) == r"scripts\generate_sample_data.py":
        return "data-tests"
    return "parent"


rows = []
for path in sorted(ROOT.rglob("*")):
    if path.is_file():
        relative = path.relative_to(ROOT)
        content = path.read_bytes()
        rows.append({
            "path": str(relative),
            "bytes": len(content),
            "lines": len(content.splitlines()),
            "sha256": hashlib.sha256(content).hexdigest(),
            "owner": owner(relative),
        })
payload = {"root": str(ROOT), "files": rows, "fileCount": len(rows)}
baseline = DEST / "inventory.json"
if baseline.exists():
    original = json.loads(baseline.read_text(encoding="utf-8"))
    previous = {row["path"]: row["sha256"] for row in original["files"]}
    current = {row["path"]: row["sha256"] for row in rows}
    assert previous == current, "File inventory/content changed during read-only review"
    print(f"UNCHANGED: {len(rows)} file hashes match baseline")
else:
    baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"BASELINE: {len(rows)} files")
for group in ("parent", "agents-docs", "data-tests"):
    selected = [row for row in rows if row["owner"] == group]
    print(group, len(selected), "files", sum(row["lines"] for row in selected), "lines")
