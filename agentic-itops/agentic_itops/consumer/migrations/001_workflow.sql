-- SQL SERVER ONLY. Explicit bootstrap; no database creation or permission grants.
-- Use a separately authorized migration identity. Runtime needs SELECT/INSERT on
-- ledgers, SELECT/INSERT/UPDATE on jobs, and transaction-owned sp_getapplock.
-- BIN2 preserves case-sensitive opaque run/event/worker identity. Producer keys
-- are derived from case-normalized configured workspace resource IDs in Python.
-- Run/kind lookup hashes also preserve trailing spaces: even BIN2 SQL equality
-- pads strings, unlike opaque Python identities. Schema version 1 is this file.
IF OBJECT_ID(N'dbo.consumer_schema', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.consumer_schema (version int NOT NULL PRIMARY KEY);
    CREATE TABLE dbo.consumer_events (
        event_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
        producer_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        event_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
        run_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
        observed_at datetime2(7) NOT NULL,
        source_sha256 char(64) NOT NULL,
        observation_sha256 char(64) NOT NULL,
        observation_json nvarchar(max) NOT NULL CHECK (ISJSON(observation_json)=1),
        CONSTRAINT UQ_consumer_event UNIQUE (producer_key, event_id)
    );
    CREATE TABLE dbo.consumer_batches (
        batch_id char(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
        producer_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        run_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
        run_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        window_key char(64) NOT NULL,
        window_json nvarchar(max) NOT NULL CHECK (ISJSON(window_json)=1),
        observed_start datetime2(7) NOT NULL,
        observed_end datetime2(7) NOT NULL,
        ingestion_start datetime2(7) NOT NULL,
        ingestion_end datetime2(7) NOT NULL,
        artifact_sha256 char(64) NOT NULL,
        artifact_bytes bigint NOT NULL CHECK (artifact_bytes >= 0),
        evidence_key nvarchar(2048) NOT NULL,
        coverage varchar(32) NOT NULL CHECK (coverage IN ('observations_present', 'no_observations')),
        receipt_json nvarchar(max) NOT NULL CHECK (ISJSON(receipt_json)=1),
        committed_at datetime2(7) NOT NULL,
        CHECK (observed_start < observed_end AND ingestion_start < ingestion_end),
        CONSTRAINT UQ_consumer_batch_revision UNIQUE (producer_key, window_key, artifact_sha256)
    );
    CREATE TABLE dbo.consumer_batch_events (
        batch_id char(64) COLLATE Latin1_General_100_BIN2 NOT NULL
            REFERENCES dbo.consumer_batches(batch_id),
        event_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL
            REFERENCES dbo.consumer_events(event_key),
        PRIMARY KEY (batch_id, event_key)
    );
    CREATE TABLE dbo.consumer_jobs (
        job_id char(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
        kind nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        kind_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        logical_key nvarchar(1024) COLLATE Latin1_General_100_BIN2 NOT NULL,
        payload_json nvarchar(max) NOT NULL CHECK (ISJSON(payload_json)=1),
        payload_sha256 char(64) NOT NULL,
        status varchar(16) NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'failed')),
        owner nvarchar(256) COLLATE Latin1_General_100_BIN2 NULL,
        fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
        expires_at datetime2(7) NULL,
        created_at datetime2(7) NOT NULL,
        finished_at datetime2(7) NULL,
        error_code nvarchar(128) NULL,
        error_message nvarchar(4000) NULL,
        CHECK ((status='leased' AND owner IS NOT NULL AND expires_at IS NOT NULL)
               OR (status<>'leased' AND expires_at IS NULL)),
        CHECK ((status='failed' AND error_code IS NOT NULL AND error_message IS NOT NULL)
               OR status<>'failed')
    );
    -- job_id is SHA256(canonical([kind, logical_key])); a hash match is always
    -- checked against both original strings and the canonical payload digest.
    CREATE TABLE dbo.consumer_results (
        job_id char(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY
            REFERENCES dbo.consumer_jobs(job_id),
        fencing_token bigint NOT NULL,
        result_json nvarchar(max) NOT NULL CHECK (ISJSON(result_json)=1),
        result_sha256 char(64) NOT NULL,
        next_job_ids_json nvarchar(max) NOT NULL CHECK (ISJSON(next_job_ids_json)=1),
        completed_at datetime2(7) NOT NULL
    );
    CREATE INDEX IX_consumer_jobs_claim
        ON dbo.consumer_jobs(kind_key, status, created_at, job_id) INCLUDE (expires_at);
    CREATE INDEX IX_consumer_batches_scope
        ON dbo.consumer_batches(producer_key, run_key, observed_start, observed_end);
    INSERT INTO dbo.consumer_schema(version) VALUES (1);
END;
IF NOT EXISTS (SELECT 1 FROM dbo.consumer_schema WHERE version=1)
    THROW 51001, 'Unsupported consumer schema version', 1;
GO
CREATE OR ALTER TRIGGER dbo.consumer_events_immutable ON dbo.consumer_events
AFTER UPDATE, DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, 'Immutable consumer event ledger', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.consumer_batches_immutable ON dbo.consumer_batches
AFTER UPDATE, DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, 'Immutable consumer batch ledger', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.consumer_batch_events_immutable ON dbo.consumer_batch_events
AFTER UPDATE, DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, 'Immutable consumer batch membership', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.consumer_results_immutable ON dbo.consumer_results
AFTER UPDATE, DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, 'Immutable consumer result ledger', 1;
END;
