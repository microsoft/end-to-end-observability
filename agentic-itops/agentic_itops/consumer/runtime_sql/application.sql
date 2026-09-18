IF OBJECT_ID('dbo.consumer_application_runs', 'U') IS NULL
CREATE TABLE dbo.consumer_application_runs (
    workflow_id char(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    incident_id varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    revision int NOT NULL CHECK (revision > 0),
    producer_key char(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
    run_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
    candidate_json nvarchar(max) NOT NULL CHECK (ISJSON(candidate_json)=1),
    portfolio_hash varchar(64) NOT NULL,
    job_kind varchar(128) NOT NULL,
    fencing_token bigint NOT NULL,
    result_json nvarchar(max) NULL CHECK (result_json IS NULL OR ISJSON(result_json)=1),
    projected bit NOT NULL,
    CONSTRAINT CK_consumer_application_result CHECK (projected=0 OR result_json IS NOT NULL),
    CONSTRAINT UQ_consumer_application_revision UNIQUE (incident_id, revision),
    CONSTRAINT FK_consumer_application_job FOREIGN KEY (workflow_id) REFERENCES dbo.consumer_jobs(job_id)
);
-- batch
CREATE OR ALTER TRIGGER dbo.consumer_application_runs_immutable
ON dbo.consumer_application_runs AFTER UPDATE, DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (
        SELECT 1 FROM deleted d LEFT JOIN inserted i ON i.workflow_id=d.workflow_id
        WHERE i.workflow_id IS NULL
           OR i.incident_id<>d.incident_id OR i.revision<>d.revision
           OR i.producer_key<>d.producer_key OR i.portfolio_hash<>d.portfolio_hash
           OR i.job_kind<>d.job_kind OR i.fencing_token<>d.fencing_token
           OR HASHBYTES('SHA2_256', CONVERT(varbinary(max), i.run_id))
              <>HASHBYTES('SHA2_256', CONVERT(varbinary(max), d.run_id))
           OR HASHBYTES('SHA2_256', CONVERT(varbinary(max), i.candidate_json))
              <>HASHBYTES('SHA2_256', CONVERT(varbinary(max), d.candidate_json))
           OR (d.result_json IS NOT NULL AND
               (i.result_json IS NULL OR HASHBYTES('SHA2_256', CONVERT(varbinary(max), i.result_json))
                <>HASHBYTES('SHA2_256', CONVERT(varbinary(max), d.result_json))))
           OR i.projected<d.projected
    )
        THROW 51002, 'Immutable application attempt or final result receipt', 1;
END;
