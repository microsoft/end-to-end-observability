# SRE Security & Identity Posture

The security watch. Daily, it turns Defender, identity, Key Vault, exposure, and
container-image signals into scored, owner-routed findings — always **human-gated**.

## Contents

| File | Purpose |
| --- | --- |
| `sre-security-identity-posture.agent.md` | The scheduled-task definition (daily). Paste the body into the agent's **Scheduled task** form, run mode **Review**. |

## What it does each run

1. Defender posture (assessments, secure-score regressions).
2. Public exposure cross-referenced with non-compliance and privilege.
3. Key Vault posture (purge protection, expiring secrets/certs).
4. Identity/RBAC risk (broad Owner, stale/risky sign-ins).
5. ACR image-vulnerability findings (Story #56).
6. Write gated security findings to `SREAgentRecommendations_CL` (workbook #9).

## Always Review mode

Every proposed security/identity action is `ApprovalRequired = true`. This task
**proposes only** — it never changes RBAC, Key Vault, exposure, or Defender settings.

## Register it (manual, portal)

1. `sre.azure.com` → **Scheduled tasks** → New, name
   `sre-security-identity-posture`, cadence **daily**, run mode **Review**.
2. Paste the markdown **body** of `sre-security-identity-posture.agent.md`.
3. Leave **disabled**; enable after review.
4. Verify: workbook **#9 Security & Identity Observability** Top 10 shows rows with
   `CreatedBy = sre-agent/security-identity-posture`.

See the [Operating Pack catalog](../README.md).
