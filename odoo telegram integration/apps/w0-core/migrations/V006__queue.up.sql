-- V006 job queue (W0-05): job type registry (CONTRACTS §2), lease columns, row-level security so that
-- every role creates and executes only its own job types. Logic lives in shared/genesis_queue.
-- Status meaning: queued -> running (lease) -> done | failed (waits for run_after, retried) | dead (after max_attempts).

CREATE TABLE core.job_types (
    type           text PRIMARY KEY,
    gpu_class      text NOT NULL CHECK (gpu_class IN ('none', 'llm', 'vision')),
    creators       text[] NOT NULL,
    executors      text[] NOT NULL,
    lease_seconds  integer NOT NULL DEFAULT 300 CHECK (lease_seconds > 0),
    max_attempts   integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    description    text
);
COMMENT ON TABLE core.job_types IS 'Job turlari (CONTRACTS §2): kim yaratadi, kim bajaradi. RLS shu jadvalga tayanadi';
COMMENT ON COLUMN core.job_types.creators IS 'INSERT qila oladigan DB rollari (w0_admin doim)';
COMMENT ON COLUMN core.job_types.executors IS 'Oladigan (claim) va yakunlaydigan DB rollari';

INSERT INTO core.job_types (type, gpu_class, creators, executors, lease_seconds, description) VALUES
  ('transcribe',       'none',   '{w1_ingest}',  '{w2_analyst}',               300, 'Ovoz/video -> matn (tashqi API)'),
  ('analyze_image',    'llm',    '{w1_ingest}',  '{w2_analyst}',               300, 'Rasm tahlili (Gemma)'),
  ('analyze_episode',  'llm',    '{w2_analyst}', '{w2_analyst}',               300, 'Episode tasnifi (Gemma)'),
  ('build_context',    'llm',    '{w2_analyst}', '{w2_analyst}',               300, '24 soatlik kontekst (Gemma)'),
  ('daily_close',      'none',   '{w0_admin}',   '{w2_analyst,w3_dashboard}',  600, 'Kun yopilishi; har iste''molchiga alohida job (target_role)'),
  ('build_excel_tg',   'none',   '{w0_admin}',   '{w3_dashboard}',             600, 'Telegram kunlik Excel'),
  ('camera_day_batch', 'vision', '{w0_admin}',   '{w4_camera}',                900, 'Kamera kunlik tahlili (YOLO); uzoq ish, heartbeat bilan'),
  ('build_report_cam', 'none',   '{w0_admin}',   '{w3_dashboard}',             600, 'Kamera hisoboti');

ALTER TABLE core.jobs
    ADD COLUMN lease_until  timestamptz,
    ADD COLUMN target_role  text,
    ADD COLUMN result       jsonb,
    ADD CONSTRAINT jobs_type_fkey FOREIGN KEY (type) REFERENCES core.job_types (type);
COMMENT ON COLUMN core.jobs.lease_until IS 'running job egasining muddati; o''tsa job qaytadi (ishchi o''lgan) yoki dead';
COMMENT ON COLUMN core.jobs.target_role IS 'Faqat shu rol olsin (masalan daily_close: w2_analyst yoki w3_dashboard). NULL = turning har qanday bajaruvchisi';
COMMENT ON COLUMN core.jobs.status IS 'queued | running | done | failed (run_after da qayta urinadi) | dead (max_attempts dan keyin, qo''lda qayta navbat)';

DROP INDEX core.jobs_ready;
CREATE INDEX jobs_ready ON core.jobs (type, priority DESC, run_after) WHERE status IN ('queued', 'failed');
CREATE INDEX jobs_lease ON core.jobs (lease_until) WHERE status = 'running';

-- Row-level security: owner (genesis_migrator) and views bypass it; everyone else is filtered.
ALTER TABLE core.jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY jobs_admin ON core.jobs TO w0_admin USING (true) WITH CHECK (true);

CREATE POLICY jobs_insert ON core.jobs FOR INSERT TO w1_ingest, w2_analyst, w3_dashboard, w4_camera
    WITH CHECK (EXISTS (SELECT 1 FROM core.job_types t WHERE t.type = jobs.type AND current_user = ANY (t.creators)));

CREATE POLICY jobs_select ON core.jobs FOR SELECT TO w1_ingest, w2_analyst, w3_dashboard, w4_camera
    USING (EXISTS (SELECT 1 FROM core.job_types t WHERE t.type = jobs.type
                   AND (current_user = ANY (t.creators)
                        OR (current_user = ANY (t.executors) AND (jobs.target_role IS NULL OR jobs.target_role = current_user)))));

CREATE POLICY jobs_update ON core.jobs FOR UPDATE TO w1_ingest, w2_analyst, w3_dashboard, w4_camera
    USING (EXISTS (SELECT 1 FROM core.job_types t WHERE t.type = jobs.type AND current_user = ANY (t.executors))
           AND (jobs.target_role IS NULL OR jobs.target_role = current_user))
    WITH CHECK (EXISTS (SELECT 1 FROM core.job_types t WHERE t.type = jobs.type AND current_user = ANY (t.executors))
                AND (jobs.target_role IS NULL OR jobs.target_role = current_user));
