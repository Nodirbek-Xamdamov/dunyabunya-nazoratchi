DROP POLICY jobs_update ON core.jobs;
DROP POLICY jobs_select ON core.jobs;
DROP POLICY jobs_insert ON core.jobs;
DROP POLICY jobs_admin ON core.jobs;
ALTER TABLE core.jobs DISABLE ROW LEVEL SECURITY;
DROP INDEX core.jobs_lease;
DROP INDEX core.jobs_ready;
CREATE INDEX jobs_ready ON core.jobs (gpu_class, priority DESC, run_after) WHERE status = 'queued';
ALTER TABLE core.jobs
    DROP CONSTRAINT jobs_type_fkey,
    DROP COLUMN result,
    DROP COLUMN target_role,
    DROP COLUMN lease_until;
DROP TABLE core.job_types;
