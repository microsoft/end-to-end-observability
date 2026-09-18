"""Generate editable diagrams of the proposed logical outcome flow."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "outputs" / "agentic-itops-outcomes" / "docs"
COLORS = {
    "source": ("#0078D4", "#CFE4FA"),
    "code": ("#F7630C", "#FFF4CE"),
    "agent": ("#107C10", "#DFF6DD"),
    "result": ("#5C2D91", "#E8DAEF"),
}
NODES = [
    ("producer", 40, 140, "Domains V2 (unchanged)\nLog Analytics observations\nNo alerts or audit labels", "source"),
    ("reader", 380, 140, "Consumer Function worker\nTyped complete windows\nEventId dedup / Blob evidence", "code"),
    ("detector", 720, 140, "Domain detector + 01\nFeatures / baseline / findings\nStable incident correlation", "code"),
    ("context", 1060, 140, "10 Industry + 02 Triage\nCriticality and prohibitions\nContext, not physical truth", "agent"),
    ("ops", 1400, 140, "00 Ops Orchestrator\nActual Foundry prompt agent\nFour specialist A2A bindings", "agent"),
    ("memory", 40, 500, "04 Runbook Memory\nReviewed Search knowledge\nExact revisions / abstention", "agent"),
    ("rca", 380, 500, "03 RCA Investigator\nEvidence and contradictions\nHypotheses, not certainty", "agent"),
    ("planner", 720, 500, "05 Remediation Planner\nExact target / component\nProposal, not execution", "agent"),
    ("gate", 1060, 500, "06 Trusted code gate\nProposal-bound receipt\nExecution always prohibited", "code"),
    ("comms", 1400, 500, "07 Incident Comms\nAfter verified gate result\nAudience drafts; never send", "agent"),
    ("search", 40, 960, "Approved publisher + Search\nSynthetic knowledge revisions\nReview / expiry / revocation", "result"),
    ("closure", 380, 960, "08 Postmortem Drafter\nDeliberate reviewed closure\nDraft and evidence lineage", "agent"),
    ("review", 720, 960, "Separate review UI / API\nEntra + App Service\nReview only; no Execute", "result"),
    ("state", 1060, 960, "Azure SQL + Blob\nResults / reviews / outbox\nImmutable evidence / leases", "result"),
    ("validate", 1400, 960, "Trusted result processor\nSchemas / citations / scope\nAtomic result + next work", "code"),
    ("hygiene", 720, 1360, "09 Alert Hygiene + code\nDetector transition history\nReviewed noise report", "agent"),
    ("tools", 1400, 1360, "Authenticated tools / MCP\nSix code operations\nNo fictional ARM authority", "code"),
]
EDGES = [
    ("producer", "reader", "read only", None),
    ("reader", "detector", "complete evidence", None),
    ("detector", "context", "derived incident", None),
    ("context", "ops", "triage handoff", None),
    ("ops", "memory", "00 coordinates this sequence", [(1560, 400), (200, 400)]),
    ("memory", "rca", "00 next", None),
    ("rca", "planner", "00 next", None),
    ("planner", "gate", "exact proposal", None),
    ("gate", "comms", "00 last", None),
    ("comms", "validate", "untrusted candidate", None),
    ("validate", "state", "validated or incomplete", None),
    ("state", "review", "persisted views", None),
    ("review", "closure", "explicit closure", None),
    ("closure", "validate", "draft candidate", [(540, 875), (1560, 875)]),
    ("search", "memory", "reviewed retrieval", None),
    ("state", "hygiene", "bounded history job", [(1220, 1290), (880, 1290)]),
    ("hygiene", "validate", "report candidate", [(880, 1260), (1560, 1260)]),
    ("review", "state", "review exact revision", [(880, 1180), (1220, 1180)]),
    ("state", "search", "approved publication outbox", [(1220, 1230), (200, 1230)]),
    ("search", "state", "indexing / revocation status", [(200, 910), (1220, 910)]),
    ("tools", "validate", "receipts / typed evidence", None),
]

mxfile = ET.Element("mxfile", host="app.diagrams.net")
diagram = ET.SubElement(mxfile, "diagram", id="outcome-flow", name="Outcome flow")
model = ET.SubElement(diagram, "mxGraphModel", grid="1", page="0")
root = ET.SubElement(model, "root")
ET.SubElement(root, "mxCell", id="0")
ET.SubElement(root, "mxCell", id="1", parent="0")
elements = []
positions = {}


def text_element(identifier, x, y, text, size=14, container=None):
    lines = text.splitlines()
    value = {
        "id": identifier, "type": "text", "x": x, "y": y,
        "width": 310 if container else max(310, max(map(len, lines)) * size),
        "height": size * 2.5 * len(lines), "text": text, "originalText": text,
        "fontSize": size, "fontFamily": 2, "strokeColor": "#000000",
        "textAlign": "center" if container else "left", "verticalAlign": "middle",
        "lineHeight": 1.25, "containerId": container, "angle": 0, "opacity": 100,
        "isDeleted": False, "version": 1, "seed": len(elements) + 1,
    }
    elements.append(value)


def annotation(identifier, x, y, width, height, text, size=20):
    cell = ET.SubElement(root, "mxCell", id=identifier, value=text, vertex="1",
                         parent="1", style=f"text;html=0;fontColor=#000000;fontSize={size};align=left;")
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(width),
                  height=str(height), attrib={"as": "geometry"})
    text_element(identifier, x, y, text, size)


annotation("title", 40, 10, 1750, 100,
           "Domains V2 -> all-nine Foundry portfolio -> operator review and learning\nPROPOSED logical sequence. Synthetic only. No cloud deployment performed.", 22)
annotation("sequence-note", 40, 730, 1700, 130,
           "Arrows are logical handoffs. 00 coordinates specialists; they do not call each other.\nNative A2A or a guarded gateway must prove intermediate validation and budget control at P0.\nSearch/publisher and SQL/Blob are grouped for readability, not single resources.", 17)

for identifier, x, y, text, kind in NODES:
    stroke, fill = COLORS[kind]
    positions[identifier] = (x, y)
    cell = ET.SubElement(root, "mxCell", id=identifier, value=text, vertex="1", parent="1",
                         style=f"rounded=1;whiteSpace=wrap;html=0;fillColor={fill};strokeColor={stroke};fontColor=#000000;fontSize=16;")
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width="320", height="170",
                  attrib={"as": "geometry"})
    elements.append({
        "id": identifier, "type": "rectangle", "x": x, "y": y,
        "width": 320, "height": 170, "strokeColor": stroke, "backgroundColor": fill,
        "fillStyle": "solid", "strokeWidth": 2, "roughness": 0,
        "roundness": {"type": 3}, "angle": 0, "opacity": 100,
        "boundElements": [{"type": "text", "id": identifier + "-label"}],
        "version": 1, "isDeleted": False, "seed": len(elements) + 1,
    })
    text_element(identifier + "-label", x + 5, y + 30, text, container=identifier)

for index, (source, target, label, waypoints) in enumerate(EDGES):
    edge_id = f"edge-{index}"
    cell = ET.SubElement(root, "mxCell", id=edge_id, value=label, edge="1",
                         parent="1", source=source, target=target,
                         style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=0;endArrow=block;fontColor=#000000;fontSize=13;")
    geometry = ET.SubElement(cell, "mxGeometry", relative="1", attrib={"as": "geometry"})
    if waypoints:
        points = ET.SubElement(geometry, "Array", attrib={"as": "points"})
        for x, y in waypoints:
            ET.SubElement(points, "mxPoint", x=str(x), y=str(y))
    sx, sy = positions[source]
    tx, ty = positions[target]
    if waypoints:
        def anchor(x, y, reference):
            dx, dy = reference[0] - (x + 160), reference[1] - (y + 85)
            if abs(dx) / 160 > abs(dy) / 85:
                return x + (320 if dx > 0 else 0), y + 85
            return x + 160, y + (170 if dy > 0 else 0)
        start = anchor(sx, sy, waypoints[0])
        end = anchor(tx, ty, waypoints[-1])
    elif sy == ty:
        start = (sx + (320 if tx > sx else 0), sy + 85)
        end = (tx + (0 if tx > sx else 320), ty + 85)
    else:
        start = (sx + 160, sy + (170 if ty > sy else 0))
        end = (tx + 160, ty + (0 if ty > sy else 170))
    path = [start, *(waypoints or []), end]
    rel = [[x - start[0], y - start[1]] for x, y in path]
    elements.append({
        "id": edge_id, "type": "arrow", "x": start[0], "y": start[1],
        "width": max(p[0] for p in rel) - min(p[0] for p in rel),
        "height": max(p[1] for p in rel) - min(p[1] for p in rel),
        "points": rel, "strokeColor": "#495057", "strokeWidth": 2,
        "endArrowhead": "arrow", "roughness": 0, "angle": 0, "opacity": 100,
        "version": 1, "isDeleted": False, "seed": len(elements) + 1,
    })

annotation("safety", 40, 1570, 1720, 100,
           "OperationalUse=prohibited | execution_allowed=false | Review acceptance is not execution permission.\nAudit labels stay outside inference. No producer controls, automatic sending or live suppression.", 19)
DOCS.mkdir(parents=True, exist_ok=True)
ET.indent(mxfile)
ET.ElementTree(mxfile).write(DOCS / "architecture-diagram.drawio", encoding="utf-8", xml_declaration=True)
(ROOT / "architecture-diagram.excalidraw").write_text(
    json.dumps({"type": "excalidraw", "version": 2, "source": "copilot",
                "elements": elements, "appState": {"viewBackgroundColor": "#ffffff"}, "files": {}},
               ensure_ascii=True, indent=2), encoding="utf-8")
assert len(positions) == 17
assert len({e["id"] for e in elements}) == len(elements)
assert all(e["width"] > 0 and e["height"] > 0 and e["strokeColor"] == "#000000"
           for e in elements if e["type"] == "text")
print(f"Generated draw.io and Excalidraw: {len(positions)} nodes, {len(EDGES)} edges.")
