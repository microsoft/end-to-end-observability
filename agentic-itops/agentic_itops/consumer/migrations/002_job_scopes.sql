-- Additive SQL SERVER migration. No queued-job identity is inferred in SQL:
-- Python canonical SHA256 is UTF-8, unlike SQL's ordinary NVARCHAR HASHBYTES.
-- Existing jobs remain NULL and cannot be scoped-claimed until an operator
-- explicitly runs store.backfill_job_scopes() using their persisted payloads.
-- Unscoped jobs remain usable only by standalone claim(scopes=None).
-- Fresh deployments apply 001 then 002; no destructive prototype reset required.
IF COL_LENGTH(N'dbo.consumer_jobs', N'scope_key') IS NULL
    ALTER TABLE dbo.consumer_jobs ADD scope_key char(64) COLLATE Latin1_General_100_BIN2 NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id=OBJECT_ID(N'dbo.consumer_jobs') AND name=N'IX_consumer_jobs_scoped_claim')
    CREATE INDEX IX_consumer_jobs_scoped_claim
        ON dbo.consumer_jobs(kind_key, scope_key, status, created_at, job_id) INCLUDE (expires_at);
IF NOT EXISTS (SELECT 1 FROM dbo.consumer_schema WHERE version=2)
    INSERT INTO dbo.consumer_schema(version) VALUES (2);
