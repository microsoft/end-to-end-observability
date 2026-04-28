# End-to-end Azure Observability Accelerator

A solution accelerator that helps customers **discover**, **deploy**, and **govern**
their Azure monitoring infrastructure and policies.

## Components

| Component | Status | Purpose |
|---|---|---|
| **Discovery** (`discovery/`) | In progress (this branch) | Read-only scan of an existing Azure estate; emits monitoring recommendations. |
| Deploy | Planned | Provisions recommended monitoring infrastructure (Bicep / azd). |
| Govern | Planned | Azure Policy assignments and drift detection. |

---

## Discovery tool (`discovery/`)

Scans an Azure subscription or management group via **Azure Resource Graph (ARG)**
and produces JSON + Markdown reports describing observability gaps and
recommended remediations.

### Why Azure Resource Graph?

ARG was chosen over `az` CLI loops or per-resource ARM REST calls because:

- **Scale**: a single KQL query covers an entire tenant; CLI loops would issue
  thousands of throttled ARM calls.
- **Joinable inventory**: ARG lets us join resources to their
  `microsoft.insights/diagnosticsettings` extension resources in one pass,
  which is exactly the data needed to detect monitoring gaps.
- **Read-only safety**: ARG is query-only; the tool only ever needs the
  `Reader` role at the chosen scope.
- **Performance**: sub-second on estates with 100k+ resources.

Targeted ARM/Monitor API calls are reserved for the small set of follow-up
deep dives that ARG cannot answer (Log Analytics table-level retention,
emitted metric definitions, Advisor recommendations).

### Architecture

```
discovery/
├── __main__.py        # `python -m discovery`
├── cli.py             # argparse entry point
├── auth.py            # DefaultAzureCredential
├── graph_client.py    # azure-mgmt-resourcegraph + paging
├── queries.py         # KQL queries (inventory + diagnostic settings)
├── rules_engine.py    # YAML rules -> findings
├── report.py          # JSON + Markdown writers
├── rules/
│   └── default.yaml   # shipped recommendation rules
└── tests/
    └── test_rules_engine.py
```

Rules are **data, not code** — see [discovery/rules/default.yaml](discovery/rules/default.yaml).
Customers and downstream components can extend the rule set without forking
the tool.

### Permissions

- `Reader` at the subscription or management group scope.
- No write permissions are ever required by discovery.

### Quick start

```pwsh
# 1. Create a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Sign in (any DefaultAzureCredential source works)
az login

# 4. Run a scan against one or more subscriptions
python -m discovery scan `
    --subscription <subId> `
    --out-dir .\out

# Or scope to a management group
python -m discovery scan `
    --management-group <mgId> `
    --out-dir .\out
```

Outputs:

- `out/discovery-report.json` — machine-readable, consumed by the (future)
  deploy component to generate Bicep/Policy.
- `out/discovery-report.md` — human-readable summary grouped by resource type.

### Running the tests

Unit tests (no Azure required):

```pwsh
pip install pytest
pytest discovery/tests
```

Integration tests against a real test subscription — see
[tests/README.md](tests/README.md) for the full deploy-fixture → scan → assert → cleanup loop.

### Extending the rule set

Add a rule to [discovery/rules/default.yaml](discovery/rules/default.yaml) (or
point `--rules` at your own file). Each rule declares a `resourceType`, a list
of `signals` that must all match, a `severity`, and a list of `recommend`
actions. Supported signals are documented at the top of
[discovery/rules_engine.py](discovery/rules_engine.py).

---

## Roadmap

- [x] Discovery: ARG sweep + rules-as-data engine + reports
- [ ] Discovery: deep-dive enrichment (Log Analytics tables, Advisor, Resource Health)
- [ ] Deploy: Bicep modules generated from `discovery-report.json`
- [ ] Govern: Azure Policy assignments + drift detection
