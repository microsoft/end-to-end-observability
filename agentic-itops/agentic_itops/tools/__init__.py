"""Tool stubs.

Every function here is what a Foundry agent calls as a tool. Each has
two implementations behind one signature:

* **stub** (default) -- answers from ``data/fixtures``. No Azure, no
  credentials, no network. This is what makes the repo runnable on a
  laptop on day one of the hackathon.
* **live** -- the real call, gated behind an environment variable.

The contract is identical either way, so swapping to live is a config
change and not a rewrite. See ``docs/architecture.md`` for the
MCP-versus-OpenAPI decision.
"""

from __future__ import annotations

from .change_history import get_recent_changes
from .log_analytics import run_kql
from .resource_graph import get_resource_context
from .runbook_index import search_knowledge

__all__ = [
    "run_kql",
    "get_resource_context",
    "get_recent_changes",
    "search_knowledge",
]
