# Discovery tool — testing

This folder contains everything needed to validate the `discovery/` tool,
both offline (unit tests) and against a real Azure test subscription
(integration tests + Bicep fixture).

```
discovery/tests/
├── test_rules_engine.py        # unit tests, no Azure required
├── fixtures/azure/
│   ├── main.bicep              # deterministic resources that trigger every rule
│   ├── Deploy-TestFixture.ps1  # provisions the fixture + emits expected-findings.json
│   └── Remove-TestFixture.ps1  # tears it down
├── integration/
│   └── test_expected_findings.py  # asserts the discovery report matches expectations
└── README.md
```

The unit tests run on every PR. The integration tests are **opt-in** and
gated by environment variables, so they will not run by accident.

---

## Prerequisites

- An isolated **test subscription** (tagged `purpose=obs-accelerator-test`).
- A small **budget alert** on the subscription (~$25/mo).
- `az login` as a user with **Contributor** on the target resource group
  (only for fixture deployment — discovery itself only needs `Reader`).
- Python venv with `pip install -r requirements.txt` and `pip install pytest`.

## One-shot test loop

```pwsh
# 1. Deploy the fixture
$pw = Read-Host -AsSecureString "SQL admin password"
.\discovery\tests\fixtures\azure\Deploy-TestFixture.ps1 `
    -SubscriptionId <test-sub-id> `
    -ResourceGroupName rg-obs-discovery-test `
    -Location eastus2 `
    -SqlAdminPassword $pw

# 2. Run discovery
python -m discovery scan --subscription <test-sub-id> --out-dir .\out

# 3. Assert the report matches the fixture's expected findings
$env:OBS_TEST_REPORT   = ".\out\discovery-report.json"
$env:OBS_TEST_EXPECTED = ".\discovery\tests\fixtures\azure\expected-findings.json"
pytest discovery/tests/integration -q

# 4. Tear down (async)
.\discovery\tests\fixtures\azure\Remove-TestFixture.ps1 -SubscriptionId <test-sub-id>
```

## What the fixture deploys

| Resource | Triggers | Notes |
|---|---|---|
| `app-bad-*` (App Service F1, no diag, no `owner` tag) | APPSVC-001, TAG-001 | |
| `app-good-*` (App Service F1, diag + `owner`) | none | negative control |
| `aks-bad-*` (AKS, 1× B2s, no diag) | AKS-001 | dominant cost driver — tear down promptly |
| `sql-bad-*` / `db-bad` (Basic 5 DTU, no diag) | SQL-001 | |
| `stgbad*` (Standard_LRS, no diag) | STG-001 | |
| `kv-bad-*` (no diag) | KV-001 | |
| `kv-prt-*` (diag to storage only, no LAW) | KV-002 | |
| `law-obs-*` | shared LAW sink | |

Approx. cost if left running: **USD $1–3/day** (mostly the AKS B2s node).

## Edge cases worth running manually

| Scenario | How |
|---|---|
| Empty subscription | run `python -m discovery scan ...` against a fresh sub |
| Multi-subscription | pass `--subscription` twice |
| Management group scope | `--management-group <mgId>` (needs `Reader` on the MG) |
| Custom rules | copy `discovery/rules/default.yaml`, edit, then `--rules .\my-rules.yaml` |
| Paging | temporarily lower `_PAGE_SIZE` in `discovery/graph_client.py` to 50 |
| Insufficient permissions | run as a principal without `Reader`; confirm error is readable |

## CI guidance

- **PR pipeline**: just `pytest discovery/tests -q`. No Azure required.
- **Nightly / merge-to-main pipeline**: federated identity with `Reader` on
  the test sub + `Contributor` on the fixture RG; deploy fixture → scan →
  assert → delete RG.
