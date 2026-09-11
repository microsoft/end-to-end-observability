# Evaluations

```bash
python evaluations/build_datasets.py    # regenerate datasets
python evaluations/run_evals.py         # run everything
python evaluations/run_evals.py --suite gate
python evaluations/run_evals.py --json > results.json
```

Exit codes: `0` all passed · `1` non-blocking failures · `2` **blocking**
failures in the safety-gate suite.

## Current state

```
PASS  normalization              10/10 (100%)
PASS  correlation                  4/4 (100%)
PASS  knowledge_retrieval        10/10 (100%)   hit@3 1.0 · MRR 0.95
PASS  change_safety_gate         17/17 (100%)
TOTAL: 41/41
```

## Two kinds of evaluation

**Deterministic assertions** for the code-hosted skills. Normalization,
correlation and gate decisions have exactly one correct answer, so these are
pass/fail. A regression is a bug, not a drift in quality.

**Retrieval metrics** for the knowledge layer, where correctness is a
ranking rather than a value. Reported as hit@3 and MRR.

Expectations are hand-written, never derived from the implementation.
Datasets are generated so alert payloads stay in sync with the adapters, but
the `expect` blocks carry the judgement — otherwise the suite would only be
asserting that the code does what the code does.

## The suites

### `normalization` — 10 cases

Every adapter, plus the edge cases that matter: both Azure Monitor payload
shapes, numeric and word severity scales, `Sev0`→critical through
`Sev4`→info, resolved-state detection, and two classification traps —
a title mentioning both a security event and a host being down must classify
as **security**, and an unrecognized rule must classify as **unknown**
rather than being forced into a bucket.

### `correlation` — 4 cases

The property the whole layer exists for.

- three tools alerting on one CPU symptom collapse to one incident
- a metric-free ServiceNow ticket **attaches** to the machine-detected
  incident, rather than becoming a fourth problem
- different resources stay separate
- the full six-signal scenario resolves to three incidents

The second case is the one worth defending. `PM-2026-06-03` records three
engineers investigating the same problem in parallel because the tools and
the human ticket were never joined.

### `knowledge_retrieval` — 10 cases

Nine positive cases across every runbook and two postmortems, plus one
that matters more than the rest:

```json
{"case_id": "ret-no-match-is-correct", "expect_doc_id": "NONE"}
```

A query with no genuine match must return **nothing**. An invented runbook
is worse than no runbook, because an engineer will follow it once and never
trust the agent again. Retrieval runs with `min_score=2.0` so weak topical
matches do not creep past.

hit@3 = 1.0 on eleven documents means the plumbing works. It does not mean
retrieval is solved — say that out loud when showing these numbers.

### `change_safety_gate` — 17 cases, all blocking

These are **security tests**, not quality metrics. Every case is marked
blocking and the runner exits `2`.

Covered:

| Case | Asserts |
|---|---|
| OT boundary denies SCADA | the runbook says restart; the gate still refuses |
| Key Vault denied by scope | scope exclusion beats an allowed verb |
| unlisted verb denied | `delete` is absent from `allowed_actions` |
| `rotate_secret` denied | destructive verbs excluded by design |
| out-of-scope resource denied | allow-list is not advisory |
| bulk restart denied | `min_affected` rule fires |
| hard blast ceiling denied | **even inside a freeze window** |
| prod restart escalates | correct approval tier |
| prod failover escalates higher | `incident_commander`, not on-call |
| prod scale-out allowed | low-risk verbs are not blanket-denied |
| blast radius escalates above ceiling | graded response |
| weekend freeze escalates | recurring window |
| peak-window freeze escalates | time-of-day window |
| dev is permissive | and dev ignores prod freezes |
| read-only mode blocks everything | the pre-AIWSA#2 posture |

Every case also asserts the decision carried **reasons and an audit id**. A
silent verdict is unusable to an operator and useless to an auditor.

## What the suite has already caught

Both found during the first full run, both real:

**Hard blast-radius ceiling was downgraded by a change freeze.** The freeze
check ran before the ceiling check, so an action affecting 40 resources
against a hard limit of 25 returned ESCALATE instead of DENY — implying a
change advisory board could approve something policy says is never
permitted. Fixed by reordering; `gate-hard-blast-radius-denied` now pins a
freeze window to prevent regression.

**An empty policy silently loaded the bundled sample.** `policy or
load_policy()` treats `{}` as absent, so a failed or empty YAML load would
fall back to the sample policy and start authorizing against rules the
operator never configured. Now distinguishes `None` from empty, and an empty
policy denies everything.

The second is the better argument for the suite existing. It is invisible in
review, produces no error, and fails open.

## Adding cases

Edit `build_datasets.py`, regenerate, run. Gate cases need
`expect_effect`, and optionally `expect_tier`, `expect_rule`, `at` (pins
evaluation time) and `policy_override`.

## Not covered yet

- **No model-output evaluation.** `StubReasoner` is deterministic
  templating, not a model. It makes the loop and the governance behaviour
  testable offline and says nothing about hypothesis quality. Evaluating
  the prompt-hosted agents needs a Foundry project and graded examples,
  and belongs in Azure AI Foundry Evaluations rather than here.

  The limitation is concrete, not hypothetical: the first live deployment
  found three defects this suite could not catch, all in the seam where a
  probabilistic component meets a deterministic contract. See
  `docs/architecture.md`, "What offline testing proves".
- **No adversarial suite.** Prompt-injection resistance is argued
  structurally (the gate is code, actions are allow-listed) rather than
  tested. It should be tested.
- **Retrieval corpus is 11 documents.** Real coverage needs a customer's
  actual runbooks.

