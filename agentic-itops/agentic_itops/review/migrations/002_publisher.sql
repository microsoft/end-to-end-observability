-- Explicit, additive, one-time migration AFTER 001_review.sql.
-- Run with the migration identity while review/publisher services are stopped.
-- No Azure resources, users, grants, roles, consumer jobs or approvals are changed.
-- IMPORTANT: the direct-table repository is NOT a SQL authorization boundary.
-- The runtime-grant comment in 001 is not a safe least-privilege deployment plan.
SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF OBJECT_ID(N'review.outbox', N'U') IS NULL
    THROW 51010, 'Apply 001_review.sql before 002_publisher.sql', 1;
IF OBJECT_ID(N'review.publisher_jobs', N'U') IS NOT NULL
    THROW 51011, '002_publisher.sql is already applied; do not reapply', 1;

CREATE TABLE review.publisher_state (
    publication_id varchar(36) NOT NULL PRIMARY KEY
        REFERENCES review.publications(publication_id),
    generation bigint NOT NULL CONSTRAINT DF_review_publisher_generation DEFAULT 0 CHECK (generation >= 0),
    accepted_outbox_id varchar(36) NULL REFERENCES review.outbox(outbox_id),
    removed_generation bigint NULL,
    reconciled_at datetime2(7) NULL
);
CREATE TABLE review.publisher_jobs (
    outbox_id varchar(36) NOT NULL PRIMARY KEY REFERENCES review.outbox(outbox_id),
    publication_id varchar(36) NOT NULL REFERENCES review.publications(publication_id),
    owner varchar(36) COLLATE Latin1_General_100_BIN2 NULL,
    fencing_token bigint NOT NULL CONSTRAINT DF_review_publisher_fence DEFAULT 0 CHECK (fencing_token >= 0),
    expires_at datetime2(7) NULL,
    not_before datetime2(7) NULL
);
CREATE INDEX IX_review_publisher_publication ON review.publisher_jobs(publication_id, expires_at);
CREATE TABLE review.publisher_attempts (
    outbox_id varchar(36) NOT NULL REFERENCES review.publisher_jobs(outbox_id),
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    owner varchar(36) COLLATE Latin1_General_100_BIN2 NOT NULL,
    status varchar(32) NOT NULL CHECK
        (status IN ('claimed','inflight','uncertain','abandoned','failed','rejected',
                    'accepted','removed','removal-pending','late')),
    receipt_json nvarchar(max) NOT NULL CHECK (ISJSON(receipt_json) = 1),
    started_generation bigint NULL,
    CONSTRAINT PK_review_publisher_attempts PRIMARY KEY (outbox_id, fencing_token)
);

INSERT INTO review.publisher_state (publication_id, generation)
SELECT publication_id, 0 FROM review.publications;

-- Preserve original outbox IDs/status/payload and immutable approval receipts.
-- Consumer invalidation jobs and unknown kinds are deliberately not enlisted.
INSERT INTO review.publisher_jobs (outbox_id, publication_id, fencing_token)
SELECT o.outbox_id, p.publication_id, 0
FROM review.outbox o
JOIN review.publications p
    ON p.publication_id COLLATE Latin1_General_100_BIN2 =
       JSON_VALUE(o.payload_json, '$.publication_id') COLLATE Latin1_General_100_BIN2
WHERE o.kind COLLATE Latin1_General_100_BIN2 IN ('publish-approved-revision', 'exclude-publication');

IF EXISTS (
    SELECT 1 FROM review.outbox o
    WHERE o.kind COLLATE Latin1_General_100_BIN2 IN ('publish-approved-revision', 'exclude-publication')
      AND NOT EXISTS (SELECT 1 FROM review.publisher_jobs j WHERE j.outbox_id = o.outbox_id)
)
    THROW 51012, 'Publisher outbox references an unknown publication; repair ledger before migration', 1;

COMMIT TRANSACTION;
