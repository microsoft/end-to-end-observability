-- Run once with the migration identity, not the runtime web identity.
-- Runtime needs SELECT/INSERT/UPDATE on review tables and sp_getapplock.
-- No consumer tables, producer resources, logins, roles or secrets are modified.
IF SCHEMA_ID(N'review') IS NULL EXEC(N'CREATE SCHEMA review');
GO
CREATE TABLE review.incidents (
    incident_id varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    current_revision int NOT NULL CHECK (current_revision > 0),
    source_json nvarchar(max) NOT NULL CHECK (ISJSON(source_json)=1),
    review_state varchar(32) NOT NULL,
    cas_version bigint NOT NULL CHECK (cas_version > 0)
);
CREATE TABLE review.revisions (
    incident_id varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL,
    revision int NOT NULL CHECK (revision > 0),
    result_id varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
    result_hash char(64) NOT NULL,
    payload_json nvarchar(max) NOT NULL CHECK (ISJSON(payload_json)=1),
    eligible bit NOT NULL,
    CONSTRAINT PK_review_revisions PRIMARY KEY (incident_id, revision),
    CONSTRAINT FK_review_revision_incident FOREIGN KEY (incident_id) REFERENCES review.incidents(incident_id)
);
CREATE TABLE review.receipts (
    receipt_id varchar(36) NOT NULL PRIMARY KEY,
    actor varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL,
    route varchar(250) COLLATE Latin1_General_100_BIN2 NOT NULL,
    idempotency_key varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL,
    payload_hash char(64) NOT NULL,
    receipt_json nvarchar(max) NOT NULL CHECK (ISJSON(receipt_json)=1),
    CONSTRAINT UQ_review_receipts UNIQUE (actor, route, idempotency_key)
);
CREATE TABLE review.publications (
    publication_id varchar(36) NOT NULL PRIMARY KEY,
    incident_id varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL,
    revision int NOT NULL,
    payload_json nvarchar(max) NOT NULL CHECK (ISJSON(payload_json)=1),
    status varchar(32) NOT NULL,
    eligible bit NOT NULL,
    eligible_until varchar(40) NOT NULL,
    CONSTRAINT UQ_review_publications UNIQUE (incident_id, revision),
    CONSTRAINT FK_review_publication_revision FOREIGN KEY (incident_id, revision)
        REFERENCES review.revisions(incident_id, revision)
);
CREATE TABLE review.outbox (
    outbox_id varchar(36) NOT NULL PRIMARY KEY,
    kind varchar(64) NOT NULL,
    payload_json nvarchar(max) NOT NULL CHECK (ISJSON(payload_json)=1),
    status varchar(32) NOT NULL
);
CREATE INDEX IX_review_outbox_status ON review.outbox(status);
CREATE TABLE review.dependencies (
    publication_id varchar(36) NOT NULL,
    incident_id varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL,
    revision int NOT NULL,
    CONSTRAINT PK_review_dependencies PRIMARY KEY (publication_id, incident_id, revision),
    CONSTRAINT FK_review_dependency_publication FOREIGN KEY (publication_id)
        REFERENCES review.publications(publication_id)
);
CREATE TABLE review.evidence_tombstones (
    evidence_id varchar(180) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    reason nvarchar(4000) NOT NULL,
    revoked_at_utc varchar(40) NOT NULL
);
GO
CREATE TRIGGER review.revisions_immutable ON review.revisions AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF UPDATE(incident_id) OR UPDATE(revision) OR UPDATE(result_id) OR UPDATE(result_hash) OR UPDATE(payload_json)
        THROW 51001, 'Immutable review revision', 1;
END;
GO
CREATE TRIGGER review.receipts_immutable ON review.receipts AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, 'Immutable review receipt', 1;
END;
GO
CREATE TRIGGER review.publications_immutable ON review.publications AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF UPDATE(publication_id) OR UPDATE(incident_id) OR UPDATE(revision) OR UPDATE(payload_json)
        THROW 51003, 'Immutable publication revision', 1;
END;
GO
