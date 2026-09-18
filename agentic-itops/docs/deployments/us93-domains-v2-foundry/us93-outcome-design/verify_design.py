"""Objective artifact checks; these do not test a deployed application."""

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "outputs" / "agentic-itops-outcomes" / "docs"
REPO = Path(__file__).resolve().parents[5]
design = (DOCS / "solution-design.md").read_text(encoding="utf-8")
delivery = (DOCS / "delivery-plan.md").read_text(encoding="utf-8")
checks = []


def check(name, condition):
    checks.append({"check": name, "passed": bool(condition)})


expected = {
    "executive-brief.md", "solution-design.md", "architecture-diagram.drawio",
    "data-assessment.md", "responsible-ai.md", "cost-estimation.md", "delivery-plan.md",
}
check("seven architecture deliverables", {p.name for p in DOCS.iterdir()} == expected)
agent_names = sorted(p.parent.name for p in (REPO / "agentic-itops" / "agents").glob("*/agent.yaml"))
check("all nine source agent names mapped", len(agent_names) == 9 and
      all(f"`{name}`" in design for name in agent_names))
check("deterministic components preserved", all(value in design for value in
      ["Normalizer 01", "Gate 06", "Hygiene 09"]))
check("five outcomes in approved goal", len(json.loads((ROOT / "goal-contract.json").read_text())["userConfirmedOutcomes"]) == 5)
check("separate review interface", "separate agentic-itops review interface" in design and
      "no Foundry code" in design)
check("immutable safety overlay", all(value in design for value in
      ["OperationalUse=prohibited", "execution_allowed=false", "no-execution", "A proposal edit invalidates"]))
check("independent ingestion and processing progress", all(value in design for value in
      ["ingestion checkpoint", "processing progress", "fencing tokens", "one SQL transaction"]))
check("rich and legacy semantics addressed", all(value in design for value in
      ["domain-details-v1", "Legacy", "inventory-to-equipment", "derived quantities"]))
check("separate lifecycle status axes", all(value in design for value in
      ["processing (", "observed lifecycle (", "human review (", "publication ("]))
check("review API conflict and idempotency", all(value in design for value in
      ["Idempotency-Key", "If-Match", "409", "CSRF"]))
check("knowledge context invalidation", all(value in design for value in
      ["revocation", "clean context", "own descendants", "expiry/applicability"]))
check("fifteen outcome acceptance rows", all(f"| E{index:02d} |" in delivery for index in range(1, 16)))
check("case-count arithmetic", 8 * 9 == 72 and 6 * 9 == 54 and
      "72 episodes" in delivery and "54 agent-level cases" in delivery)
check("person-day arithmetic", sum([3, 5, 6, 5, 5]) == 24 and
      sum([5, 8, 10, 8, 9]) == 40 and "24-40" in delivery)
check("no cloud readiness claim", "not deployed" in design and "proposed for approval" in delivery)

tree = ET.parse(DOCS / "architecture-diagram.drawio")
cells = tree.findall(".//mxCell")
vertices = {c.get("id") for c in cells if c.get("vertex") == "1"}
edges = [c for c in cells if c.get("edge") == "1"]
check("drawio topology references", len(edges) == 21 and all(
    c.get("source") in vertices and c.get("target") in vertices for c in edges))
check("all branch outputs pass through validation and review", {
    ("closure", "validate"), ("hygiene", "validate"), ("review", "state"), ("state", "search")
}.issubset({(c.get("source"), c.get("target")) for c in edges}))
check("late corrections propagate to learning", all(value in design for value in
      ["reverse lineage", "derived knowledge descendants", "needs_evidence", "new review"]))
check("retention bounds knowledge eligibility", all(value in " ".join(design.split()) for value in
      ["eligible_until", "earliest scheduled", "Legal erasure", "no silent retention extension"]))
check("defined episode scoring and abstention", all(value in delivery for value in
      ["one-to-one", "TP/(TP+FP)", "TP/(TP+FN)", "on a supported labeled positive is FN", "not estimable"]))
chart = json.loads((ROOT / "architecture-diagram.excalidraw").read_text(encoding="utf-8"))
check("excalidraw document structure", chart["type"] == "excalidraw" and chart["version"] == 2)
check("excalidraw visible black text", all(
    e["width"] > 0 and e["height"] > 0 and e["strokeColor"] == "#000000"
    for e in chart["elements"] if e["type"] == "text"))
check("unique diagram IDs", len({e["id"] for e in chart["elements"]}) == len(chart["elements"]))
check("ASCII documentation", all(p.read_text(encoding="utf-8").isascii()
      for p in DOCS.glob("*.md")))

report = {
    "scope": "Artifact structure and explicit contract coverage, not runtime behavior or rendered diagram inspection",
    "passed": sum(c["passed"] for c in checks), "total": len(checks),
    "source_agent_names": agent_names, "checks": checks,
    "artifacts_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(DOCS.iterdir()) if p.is_file()},
}
(ROOT / "design-checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(0 if all(c["passed"] for c in checks) else 1)
