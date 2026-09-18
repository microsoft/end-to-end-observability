CREATE TABLE IF NOT EXISTS itops_runtime_month (
    scope_key TEXT NOT NULL, month_key TEXT NOT NULL, limit_nanos INTEGER NOT NULL,
    accounted_nanos INTEGER NOT NULL, PRIMARY KEY (scope_key,month_key)
);
-- batch
CREATE TABLE IF NOT EXISTS itops_runtime_workflow (
    workflow_id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL, scope_key TEXT NOT NULL,
    deadline_at TEXT NOT NULL, api_calls INTEGER NOT NULL, tool_calls INTEGER NOT NULL,
    tokens_accounted INTEGER NOT NULL, stop_reason TEXT
);
-- batch
CREATE TABLE IF NOT EXISTS itops_runtime_call (
    invocation_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, scope_key TEXT NOT NULL,
    month_key TEXT NOT NULL, agent_name TEXT NOT NULL, agent_version TEXT NOT NULL,
    request_hash TEXT NOT NULL, state TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
    reserved_nanos INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
    charged_nanos INTEGER NOT NULL
);
-- batch
CREATE TABLE IF NOT EXISTS itops_runtime_tool (
    workflow_id TEXT NOT NULL, call_id TEXT NOT NULL, tool_name TEXT NOT NULL,
    PRIMARY KEY (workflow_id,call_id)
);
