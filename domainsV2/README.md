# Energy Domains V2 portable deployment kit

Domains V2 is a copyable, fictional demo package for nine monitoring and
inventory portal surfaces. You can copy only this folder into another
repository, use it as that repository's root, and redeploy the producer portals
without depending on the original repo layout. This is a producer-only kit;
it contains no detector, model, or agent add-on deployment.

This kit does not send Azure Monitor alert notifications, dispatch engineers, or perform
operational control. All data, assets, locations, and inventory are fictional.
It includes power-grid, upstream, midstream, refinery and downstream portfolio
monitoring, plus inventory portals for all four domains.

## What is in the kit

| Path | Purpose |
| --- | --- |
| `energy_domains_v2\` | Flask app, replay runtime, packaged fixtures |
| `support\` | Bundled domain code, reviewed inventory inputs, presentations, provenance manifest |
| `infra\` | Producer Bicep, exact B2 plan template, readiness RBAC, RG bootstrap |
| `scripts\` | Portable stage runner, packaging, publish, verification helpers |
| `docs\` | Copy-only deployment/runbook guidance |
| `environment.example.json` | Config template for a new environment |

## Sharing and clean export

For handoff, prefer exporting a clean portable copy instead of manually copying
the folder and risking `.local`, `.venv`, or compiled artifacts:

```powershell
.\scripts\Export-Kit.ps1 -OutputDirectory ..\domainsV2-portable
```

`Export-Kit.ps1` verifies the bundled support manifest before copying, excludes
local state, virtual environments, compiled Bicep JSON, ZIPs, and local
operator config, then runs `scripts\Verify-Support.ps1` again in the exported
copy.

You can also run support verification directly at any time:

```powershell
.\scripts\Verify-Support.ps1
```

## Copy-only local setup

From the copied folder:

```powershell
Copy-Item .\environment.example.json .\environment.local.json
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

To run locally without Azure, from the same folder:

```powershell
$env:V2_MODE = 'local'
$env:V2_PUBLIC_ORIGIN = 'http://127.0.0.1:8880'
python -m flask --app wsgi run --host 127.0.0.1 --port 8880
```

Open `http://127.0.0.1:8880`. Logs are captured under `.local`, not sent to Azure.
Installation or app startup does not prepare a demo automatically.

`requirements.txt` installs `-e ./support`, `-e .`, and `gunicorn`. The
reviewed fixtures are already bundled; do not regenerate them for a normal
redeploy.

For local validation:

```powershell
python -m pip install -e ".[validation]"
python -m unittest discover -s tests -v
```

## Packaging and deployment flow

Use the copied folder as the working root. The default ZIP candidate is built
from this kit only; no external `ReviewDataRoot` is required.

```powershell
.\scripts\Deploy-Kit.ps1 -Stage ProducerPackage -Action Package
```

That calls `scripts\package.ps1 -OutputDirectory <configured-folder>`, which
keeps the existing package contract:

- stage directory: `<output>\stage`
- ZIP: `<output>\domains-v2.zip`
- JSON result: `{"entries":...,"bytes":...,"sha256":"..."}`

The staged ZIP root contains `energy_domains_v2`, `support`,
`pyproject.toml`, `requirements.txt`, and `app.py` copied from `wsgi.py`.

Run infrastructure one stage at a time. The portable runner intentionally has
no `All` mode so a dry run cannot imply that later stages are already valid.

1. `BootstrapResourceGroup`
2. `Plan`
3. `ProducerData`
4. `ProducerApp`
5. `Readiness`
6. `ProducerPublish`

See [docs/new-subscription-deployment.md](docs/new-subscription-deployment.md)
for exact commands, prerequisites, provider checks, approvals, and stage
semantics.

## Security and governance defaults

- Current validated/allowed region: `centralus` only.
- Producer storage stays HTTPS-only, keyless, anonymous-disabled, and fail-closed.
- The validated producer route uses an enforced Network Security Perimeter, not
  private endpoints. Storage uses its public HTTPS endpoint, gated by managed
  identity and the enforced perimeter; it is not anonymously accessible.
  The stage runner requires this mode; legacy low-level private-network defaults
  are not the portable deployment path.
- Public anonymous portal access is opt-in only. Set
  `producer.unrestrictedPortal=true` **and** pass
  `-ApprovePublicAnonymousAccess` on the `ProducerApp` provision step.
- The readiness role is observation-table-only. It must not be broadened to
  `EnergyInjectionAudit_CL` or arbitrary workspace tables.
- Provider registration is never automatic. Governance changes require an
  approved admin.

## Demo/runtime notes

- Demo duration is manually started and capped at 120 minutes, including the
  ten-minute warm-up.
- Stop drains queued delivery; it does not delete history.
- Resource charges continue after Stop until resources are removed separately.
- No enabled anomaly-alert or SRE dispatch automation is included.

## Historical note

A separate 2026-09-14 Central US deployment validated the staged sequence now
documented here. Treat that run as historical evidence only; do not reuse its
names, hashes, or approvals for a new environment.
