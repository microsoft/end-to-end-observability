# Deploying to Azure

A live deployment of the prompt-hosted agents into an Azure AI Foundry
project. Everything else in this repo runs offline; this is what to do
when you want to see the agents reasoning for real.

## What gets created

| Resource | SKU | Cost |
|---|---|---|
| Azure AI Foundry account (`Microsoft.CognitiveServices`, kind `AIServices`) | S0 | no standing charge |
| Foundry project | — | free |
| `gpt-4o` deployment | Standard, 10K TPM | pay per token |
| Vector store (11 documents) | built into the account | free |

**Idle cost is effectively zero.** Standard model deployments bill per
token, not per hour, so an unused project costs nothing. A full demo run
is a few cents.

There is deliberately **no Azure AI Search service**. See below.

## Prerequisites

```bash
az login
az account set --subscription <id>
pip install -r requirements-live.txt
```

Roles needed on the resource group: **Azure AI Developer**,
**Cognitive Services OpenAI User**, **Cognitive Services User**.

## Provision

```bash
RG=rg-agentic-itops-demo
LOC=eastus2
ACCT=aifoundry-itops-$RANDOM

az group create -n $RG -l $LOC

az cognitiveservices account create -n $ACCT -g $RG -l $LOC \
  --kind AIServices --sku S0 --custom-domain $ACCT --assign-identity --yes

az resource update \
  --ids "/subscriptions/<sub>/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCT" \
  --set properties.allowProjectManagement=true

# Project creation has no first-class CLI command yet.
# Pass the body as a file -- inline JSON gets mangled by most shells.
cat > proj.json <<'JSON'
{ "location": "eastus2",
  "identity": { "type": "SystemAssigned" },
  "properties": { "displayName": "Agentic ITOps" } }
JSON

az rest --method put \
  --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACCT/projects/agentic-itops?api-version=2025-04-01-preview" \
  --body @proj.json

az cognitiveservices account deployment create -n $ACCT -g $RG \
  --deployment-name gpt-4o --model-name gpt-4o --model-version 2024-11-20 \
  --model-format OpenAI --sku-name Standard --sku-capacity 10
```

## Deploy the agents

```bash
ENDPOINT="https://$ACCT.services.ai.azure.com/api/projects/agentic-itops"

python scripts/deploy_to_foundry.py --endpoint $ENDPOINT
python scripts/deploy_to_foundry.py --endpoint $ENDPOINT --smoke-test
```

This uploads the runbook and postmortem corpus into a vector store,
creates five agents, and writes `.foundry-agents.json`. The smoke test
asks the memory agent a real question and prints the answer.

## Run the loop against live agents

```bash
python -m agentic_itops demo --foundry
python -m agentic_itops demo --foundry --limit 2   # includes the gate denial
```

The code skills — normalizer, safety gate, hygiene — still run locally.
Only the reasoning goes to Foundry. That separation is the point: the
authorization boundary does not move just because the reasoning did.

## Two decisions worth understanding

### No Azure AI Search

The corpus is eleven markdown documents. A dedicated Search service is a
separate resource, a separate bill, a separate identity grant, and — when
this was first deployed — Free tier had no capacity in `eastus2` at all.
Foundry's built-in vector store is co-located, free with the account, and
adequate well past this scale.

Move to Azure AI Search when the corpus is a customer's real runbook
estate and hybrid semantic ranking starts to earn its cost. The agent
manifest already declares `azure_ai_search`; `deploy_to_foundry.py`
substitutes `file_search` at deploy time, so switching back is a one-line
change.

### The Responses API, not Assistants

Current Foundry projects expose **versioned agents** created via
`project.agents.create_version(...)` and invoked through the
**Responses API** with an `agent_reference`:

```python
client.responses.create(
    input=message,
    extra_body={"agent_reference": {"type": "agent_reference", "name": agent_name}},
)
```

The older Assistants/threads API returns **404** against these projects,
and the preview `azure-ai-agents` operations have moved between releases.
The Responses path is the stable one and is what the portal shows.

## Verifying it worked

The portal is at `https://ai.azure.com` — open the project and the agents
appear under **Agents**, each chattable directly.

Worth checking specifically:

1. **`runbook-memory` returns structured JSON** including a
   `prohibitions` array. If it returns prose, the vector store is not
   bound or indexing had not finished when the agent was created.
2. **The SCADA scenario still DENIES.** `demo --foundry --limit 2`. The
   gate is local code, so a live deployment must not change this. If it
   does, something is wrong with how the plan is being submitted.
3. **The comms agent reports a denial as a refusal**, not as a caveat.

## Tearing down

```bash
python scripts/deploy_to_foundry.py --endpoint $ENDPOINT --cleanup   # agents + vector store
az group delete -n rg-agentic-itops-demo --yes --no-wait             # everything
```

The resource group is tagged `project=agentic-itops` so it is easy to
find later.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `404` on agent creation | using the Assistants API; use `create_version` |
| `'agent' property is deprecated` | use `agent_reference` with `type` set |
| Agent returns prose, not JSON | instructions lack an explicit Output section |
| Memory agent finds nothing | vector store still indexing when agent was created |
| `InsufficientResourcesAvailable` on Search | Free tier capacity; not needed here anyway |
| `TokenCreatedWithOutdatedPolicies` | CAE challenge — re-run `az login` |
