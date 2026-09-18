CREATE TABLE IF NOT EXISTS consumer_application_runs (
    workflow_id TEXT NOT NULL PRIMARY KEY REFERENCES consumer_jobs(job_id),
    incident_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    producer_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    portfolio_hash TEXT NOT NULL,
    job_kind TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    result_json TEXT,
    projected INTEGER NOT NULL CHECK (projected=0 OR result_json IS NOT NULL),
    UNIQUE (incident_id, revision)
);
-- batch
CREATE TRIGGER IF NOT EXISTS consumer_application_runs_no_delete
BEFORE DELETE ON consumer_application_runs BEGIN
    SELECT RAISE(ABORT, 'Immutable application attempt');
END;
-- batch
CREATE TRIGGER IF NOT EXISTS consumer_application_runs_immutable
BEFORE UPDATE ON consumer_application_runs
WHEN NEW.workflow_id<>OLD.workflow_id OR NEW.incident_id<>OLD.incident_id
  OR NEW.revision<>OLD.revision OR NEW.producer_key<>OLD.producer_key
  OR NEW.run_id<>OLD.run_id OR NEW.candidate_json<>OLD.candidate_json
  OR NEW.portfolio_hash<>OLD.portfolio_hash OR NEW.job_kind<>OLD.job_kind
  OR NEW.fencing_token<>OLD.fencing_token OR NEW.projected<OLD.projected
  OR (OLD.result_json IS NOT NULL AND (NEW.result_json IS NULL OR NEW.result_json<>OLD.result_json))
BEGIN
    SELECT RAISE(ABORT, 'Immutable application attempt or final result receipt');
END;
