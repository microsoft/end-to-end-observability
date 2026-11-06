# Documentation

This directory holds the design and operating documents of `autonomous-sre-ops/`, a demo environment in which a
managed Azure SRE Agent investigates real Azure Monitor alerts in Review mode. Start with the
[subproject README](../README.md) for the overview, then follow the table.

| Document | Read it to learn |
|---|---|
| [deployment.md](deployment.md) | The operator sequence: prerequisites, state, plan and apply, images, agent publication, checks |
| [implementation.md](implementation.md) | Source layout, execution model, context contract, local checks, runtime images, agent-asset flow, fault controls, deferred work |
| [decisions.md](decisions.md) | The decisions that bind the source, with rationale and consequences |
| [native-review-workflows.md](native-review-workflows.md) | The agent plane: the observe-only handler, its evidence tool, the incident filter, publication, inert production assets, knowledge |
| [runtime-networking.md](runtime-networking.md) | How the managed agent reaches the private runtime, which paths are private, what to prove after deployment |

Suggested reading order for a new engineer: the README, `decisions.md`, `implementation.md`, then
`native-review-workflows.md` and `runtime-networking.md`. Use `deployment.md` when you deploy.

## Scope

Phase 1 deploys the foundation and compute, the runtime images and jobs, the optional managed agent, the alert rules
and the observe-only handler `asre-azure-monitor-observe`. ServiceNow case management, the operator dashboard and a
CI/CD pipeline are deferred: their source and tests exist, but nothing in Phase 1 deploys or enables them.

## Source-of-truth rules

- **Code and Terraform are authoritative.** The documents explain the design and its limits. If a document and the
  source disagree, correct whichever is wrong in the same change.
- **Update the owning document when behaviour changes.** A change to Terraform inputs or the context contract belongs
  in `implementation.md`; a new or changed decision in `decisions.md`; an agent, tool, filter or publication change in
  `native-review-workflows.md`; a network or identity change in `runtime-networking.md`; a change to the operator steps
  in `deployment.md`.
- **Separate what the source guarantees from what a deployment must prove.** Offline tests and `terraform test` do not
  show Azure behaviour. Say "not yet exercised in a live deployment" where a statement depends on Azure behaviour that
  the source cannot show.
- **Keep sensitive data out.** Never put secrets, keys, tokens, connection strings, private state or unreviewed logs in
  a document. Use placeholders such as `<subscription-id>`, `<tenant-id>`, `<operator-ip>` and `<suffix>`, and no real
  identifiers, instance names or addresses. Evidence you quote must be sanitized.
- **Describe, do not narrate.** Write what the source does in the present tense. Do not record the history of a
  deployment, dated status or run numbers; the version control history holds that.
- **Do not edit generated output.** Rendered agent assets, saved plans and the generated context file are generated and
  git-ignored because they carry deployment values. Change the source and regenerate.
- **Line endings are LF**, as `.gitattributes` requires.
