import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
inventory = json.loads((ROOT / "inventory.json").read_text(encoding="utf-8"))
expected = {row["path"]: row for row in inventory["files"]}
reviewed = {}
for owner in ("parent", "agents-docs", "data-tests"):
    coverage = json.loads((ROOT / f"{owner}-coverage.json").read_text(encoding="utf-8"))
    assert not coverage["unreviewed"], (owner, coverage["unreviewed"])
    for entry in coverage["files"]:
        path = entry["path"].replace("/", "\\")
        assert path in expected, f"Unexpected path: {path}"
        assert expected[path]["owner"] == owner, (path, owner)
        assert path not in reviewed, f"Duplicate review coverage: {path}"
        assert entry["review"] in ("full-content", "structured-full-scan"), entry
        assert entry["summary"], f"No review note: {path}"
        reviewed[path] = {**expected[path], **entry}
assert set(expected) == set(reviewed), f"Missing files: {sorted(set(expected) - set(reviewed))}"
summary = {
    "fileCount": len(expected),
    "reviewedCount": len(reviewed),
    "unreviewed": [],
    "sourceLines": sum(row["lines"] for row in expected.values()),
    "reviewMethods": {
        method: sum(row["review"] == method for row in reviewed.values())
        for method in ("full-content", "structured-full-scan")
    },
    "files": [reviewed[path] for path in sorted(reviewed)],
}
(ROOT / "coverage-verified.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps({key: value for key, value in summary.items() if key != "files"}, indent=2))
