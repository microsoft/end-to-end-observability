IF OBJECT_ID('itops_runtime_month','U') IS NULL
CREATE TABLE itops_runtime_month (
    scope_key varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    month_key char(7) NOT NULL,
    limit_nanos bigint NOT NULL,
    accounted_nanos bigint NOT NULL,
    PRIMARY KEY (scope_key,month_key)
);
-- batch
IF OBJECT_ID('itops_runtime_workflow','U') IS NULL
CREATE TABLE itops_runtime_workflow (
    workflow_id varchar(128) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
    policy_hash char(64) NOT NULL,
    scope_key varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    deadline_at varchar(40) NOT NULL,
    api_calls int NOT NULL,
    tool_calls int NOT NULL,
    tokens_accounted bigint NOT NULL,
    stop_reason varchar(128) NULL
);
-- batch
IF OBJECT_ID('itops_runtime_call','U') IS NULL
CREATE TABLE itops_runtime_call (
    invocation_id varchar(128) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
    workflow_id varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    scope_key varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    month_key char(7) NOT NULL,
    agent_name varchar(128) NOT NULL,
    agent_version varchar(32) NOT NULL,
    request_hash char(64) NOT NULL,
    state varchar(16) NOT NULL,
    reserved_tokens bigint NOT NULL,
    reserved_nanos bigint NOT NULL,
    input_tokens bigint NULL,
    output_tokens bigint NULL,
    charged_nanos bigint NOT NULL
);
-- batch
IF OBJECT_ID('itops_runtime_tool','U') IS NULL
CREATE TABLE itops_runtime_tool (
    workflow_id varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    call_id varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    tool_name varchar(128) NOT NULL,
    PRIMARY KEY (workflow_id,call_id)
);
