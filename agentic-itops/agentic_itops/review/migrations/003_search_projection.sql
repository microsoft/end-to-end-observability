-- Explicit additive migration after 001_review.sql and 002_publisher.sql.
-- Migration identity only; stop services. No grants or resource deployment.
-- Direct-table runtime access is NOT a SQL authorization boundary.
SET XACT_ABORT ON;
BEGIN TRANSACTION;
IF OBJECT_ID(N'review.publisher_jobs', N'U') IS NULL
    THROW 51020, 'Apply 002_publisher.sql first', 1;
CREATE TABLE review.publisher_projections (
    publication_id varchar(36) NOT NULL PRIMARY KEY REFERENCES review.publications(publication_id),
    projection_json nvarchar(4000) NOT NULL CHECK (ISJSON(projection_json)=1)
);
EXEC(N'CREATE TRIGGER review.publisher_projection_immutable ON review.publisher_projections AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51021, ''Immutable publisher snapshot and target binding'', 1;
END;');
COMMIT TRANSACTION;
