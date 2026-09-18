-- Additive deployment-owned boundary. Apply after review 001/002/003, before runtime grants.
CREATE TABLE dbo.itops_runtime_boundary_version (
    version int NOT NULL PRIMARY KEY
);
INSERT INTO dbo.itops_runtime_boundary_version(version) VALUES (1);
GO
CREATE TRIGGER review.itops_publisher_incident_boundary ON review.incidents AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF USER_NAME() = N'itops_publisher'
    BEGIN
        IF UPDATE(incident_id) OR UPDATE(current_revision) OR UPDATE(source_json)
            THROW 51100, 'Publisher cannot change incident identity or source', 1;
        IF EXISTS (
            SELECT 1 FROM inserted i JOIN deleted d ON i.incident_id=d.incident_id
            WHERE i.review_state COLLATE Latin1_General_100_BIN2 <> 'needs_evidence'
               OR DATALENGTH(i.review_state)<>DATALENGTH('needs_evidence')
               OR i.cas_version<>d.cas_version+1
        )
            THROW 51101, 'Publisher can only withdraw review eligibility', 1;
    END;
END;
GO
CREATE TRIGGER review.itops_publisher_revision_boundary ON review.revisions AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF USER_NAME() = N'itops_publisher' AND EXISTS (SELECT 1 FROM inserted WHERE eligible<>0)
        THROW 51102, 'Publisher cannot enable a review revision', 1;
END;
GO
CREATE TRIGGER review.itops_publisher_publication_boundary ON review.publications AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF USER_NAME() = N'itops_publisher'
    BEGIN
        IF UPDATE(eligible_until)
            THROW 51103, 'Publisher cannot change approval expiry', 1;
        IF EXISTS (
            SELECT 1 FROM inserted
            WHERE eligible<>0 OR status COLLATE Latin1_General_100_BIN2 <> 'revoked'
               OR DATALENGTH(status)<>DATALENGTH('revoked')
        )
            THROW 51104, 'Publisher cannot approve or reactivate a publication', 1;
    END;
END;
GO
CREATE TRIGGER review.itops_outbox_authority_boundary ON review.outbox AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (SELECT 1 FROM deleted)
    BEGIN
        IF UPDATE(outbox_id) OR UPDATE(kind) OR UPDATE(payload_json)
            THROW 51105, 'Outbox command identity and payload are immutable', 1;
        IF USER_NAME() = N'itops_publisher' AND EXISTS (
            SELECT 1 FROM inserted
            WHERE kind COLLATE Latin1_General_100_BIN2 NOT IN ('publish-approved-revision','exclude-publication')
               OR status COLLATE Latin1_General_100_BIN2 NOT IN ('leased','failed','processed','cancelled')
               OR DATALENGTH(kind)<>LEN(kind) OR DATALENGTH(status)<>LEN(status)
        )
            THROW 51106, 'Publisher can only process publisher commands with supported statuses', 1;
    END
    ELSE IF USER_NAME() = N'itops_publisher' AND EXISTS (
        SELECT 1 FROM inserted
        WHERE kind COLLATE Latin1_General_100_BIN2 NOT IN ('exclude-publication','invalidate-workflows')
           OR status COLLATE Latin1_General_100_BIN2 <> 'pending'
           OR DATALENGTH(kind)<>LEN(kind) OR DATALENGTH(status)<>DATALENGTH('pending')
    )
        THROW 51107, 'Only review authority can enqueue a publication upload; publisher revocations must start pending', 1;
END;
GO
