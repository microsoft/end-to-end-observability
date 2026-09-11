"""Runbook and institutional-memory search -- backs agent #4.

Stub mode implements BM25 over the local markdown corpus in
``data/runbooks`` and ``data/postmortems``. That is deliberately not a
toy substring match: retrieval quality is the whole value of agent #4,
so the offline path has to be good enough to evaluate honestly. The
evaluation suite scores this implementation directly.

Live mode delegates to Azure AI Search (hybrid vector + keyword). The
return contract is identical.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
CORPUS_DIRS = ("runbooks", "postmortems")

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-_]*")
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "are", "was", "were",
    "has", "have", "had", "not", "but", "you", "your", "its", "it", "a", "an",
    "to", "of", "in", "on", "is", "be", "as", "at", "or", "if", "by", "we",
}

# BM25 parameters. k1 controls term-frequency saturation, b controls
# length normalization. These are the standard defaults and behave well
# on short technical documents.
_K1 = 1.5
_B = 0.75


@dataclass
class Document:
    doc_id: str
    title: str
    kind: str          # runbook | postmortem
    path: str
    text: str
    tokens: list[str]
    symptom_classes: list[str]
    services: list[str]
    documented_actions: list[str]


#: Verbs the Change-Safety Gate knows about. Extracting these explicitly
#: means the Remediation Planner proposes what the runbook actually says,
#: rather than inferring an action from prose that happened to land in
#: the excerpt window.
KNOWN_ACTIONS = (
    "restart",
    "scale_out",
    "scale_in",
    "drain_node",
    "failover",
    "clear_cache",
    "recycle_app_pool",
    "reprocess_queue",
    "enable_diagnostic_setting",
    "annotate_incident",
)


def search_knowledge(
    query: str,
    top_k: int = 3,
    kind: str = "",
    min_score: float = 0.0,
) -> dict[str, Any]:
    """Search runbooks and postmortems.

    Args:
        query: Free text. In practice the incident title plus symptom
            class plus service name works better than the title alone.
        top_k: Number of results.
        kind: Restrict to ``runbook`` or ``postmortem``. Empty = both.
        min_score: Drop results below this BM25 score. Returning nothing
            is a valid and useful answer -- a fabricated runbook is worse
            than no runbook.

    Returns:
        ``{"results": [...], "count": n, "mode": "stub"|"live"}``
    """
    if _live():
        return _search_live(query, top_k, kind)
    return _search_stub(query, top_k, kind, min_score)


def _live() -> bool:
    return os.getenv("AGENTIC_ITOPS_LIVE_SEARCH", "").strip() in ("1", "true", "yes")


# --- BM25 stub --------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


@lru_cache(maxsize=1)
def _load_corpus() -> tuple[Document, ...]:
    docs: list[Document] = []
    for dirname in CORPUS_DIRS:
        directory = DATA_ROOT / dirname
        if not directory.exists():
            continue
        kind = "runbook" if dirname == "runbooks" else "postmortem"
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            docs.append(
                Document(
                    doc_id=path.stem,
                    title=_extract_title(text, path.stem),
                    kind=kind,
                    path=str(path.relative_to(DATA_ROOT.parent)),
                    text=text,
                    tokens=_tokenize(text),
                    symptom_classes=_extract_field(text, "symptom_class"),
                    services=_extract_field(text, "service"),
                    documented_actions=_extract_actions(text),
                )
            )
    return tuple(docs)


def _extract_actions(text: str) -> list[str]:
    """Pull the action verbs a runbook prescribes, in document order.

    Looks only at indented code-block lines, which is the convention
    these runbooks use for "the command to run". Prose mentions of a
    verb ("do not restart the host") are deliberately ignored -- reading
    a prohibition as an instruction is exactly the failure mode that
    makes people distrust agents.
    """
    actions: list[str] = []
    for line in text.splitlines():
        if not line.startswith("    "):
            continue
        candidate = line.strip().lower()
        if candidate in KNOWN_ACTIONS and candidate not in actions:
            actions.append(candidate)
    return actions


def _extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _extract_field(text: str, field: str) -> list[str]:
    """Read a front-matter-ish ``field: a, b`` line from the header block."""
    match = re.search(rf"^{field}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if not match:
        return []
    return [v.strip().lower() for v in match.group(1).split(",") if v.strip()]


def _search_stub(query: str, top_k: int, kind: str, min_score: float) -> dict[str, Any]:
    corpus = [d for d in _load_corpus() if not kind or d.kind == kind]
    if not corpus:
        return {
            "results": [],
            "count": 0,
            "mode": "stub",
            "note": "no documents in data/runbooks or data/postmortems",
        }

    query_terms = _tokenize(query)
    if not query_terms:
        return {"results": [], "count": 0, "mode": "stub"}

    doc_count = len(corpus)
    avg_len = sum(len(d.tokens) for d in corpus) / doc_count
    doc_freq: Counter[str] = Counter()
    for doc in corpus:
        for term in set(doc.tokens):
            doc_freq[term] += 1

    scored: list[tuple[float, Document, list[str]]] = []
    for doc in corpus:
        freqs = Counter(doc.tokens)
        length = len(doc.tokens) or 1
        score = 0.0
        matched: list[str] = []
        for term in query_terms:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            matched.append(term)
            idf = math.log(1 + (doc_count - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            score += idf * (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * length / avg_len))
        # Structured metadata is a stronger signal than prose. Boost when
        # the document declares the same symptom class or service.
        lowered = query.lower()
        if any(s and s in lowered for s in doc.symptom_classes):
            score *= 1.35
        if any(s and s in lowered for s in doc.services):
            score *= 1.25
        if score > 0:
            scored.append((score, doc, matched))

    scored.sort(key=lambda item: item[0], reverse=True)
    results = [
        {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "kind": doc.kind,
            "path": doc.path,
            "score": round(score, 3),
            "matched_terms": sorted(set(matched)),
            "documented_actions": doc.documented_actions,
            "excerpt": _excerpt(doc.text, matched),
        }
        for score, doc, matched in scored[:top_k]
        if score >= min_score
    ]
    return {"results": results, "count": len(results), "mode": "stub", "query": query}


def _excerpt(text: str, matched: list[str], width: int = 320) -> str:
    """Return the densest passage around the matched terms."""
    if not matched:
        return text[:width].strip()
    lowered = text.lower()
    best_pos, best_hits = 0, -1
    step = 80
    for start in range(0, max(len(text) - width, 1), step):
        window = lowered[start : start + width]
        hits = sum(window.count(term) for term in matched)
        if hits > best_hits:
            best_hits, best_pos = hits, start
    snippet = text[best_pos : best_pos + width].strip()
    return ("..." if best_pos else "") + snippet + ("..." if best_pos + width < len(text) else "")


# --- live -------------------------------------------------------------


def _search_live(query: str, top_k: int, kind: str) -> dict[str, Any]:
    from azure.core.credentials import AzureKeyCredential  # noqa: PLC0415
    from azure.search.documents import SearchClient  # noqa: PLC0415

    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    index = os.environ.get("AZURE_SEARCH_INDEX", "agentic-itops-knowledge")
    key = os.environ["AZURE_SEARCH_API_KEY"]

    client = SearchClient(endpoint, index, AzureKeyCredential(key))
    filter_expr = f"kind eq '{kind}'" if kind else None
    found = client.search(search_text=query, top=top_k, filter=filter_expr)

    results = [
        {
            "doc_id": doc.get("id", ""),
            "title": doc.get("title", ""),
            "kind": doc.get("kind", ""),
            "path": doc.get("path", ""),
            "score": doc.get("@search.score", 0.0),
            "matched_terms": [],
            "excerpt": (doc.get("content") or "")[:320],
        }
        for doc in found
    ]
    return {"results": results, "count": len(results), "mode": "live", "query": query}
