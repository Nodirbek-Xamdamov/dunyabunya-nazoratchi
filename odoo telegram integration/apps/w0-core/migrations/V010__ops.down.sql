DROP VIEW api.v_alerts;
DROP VIEW api.v_system_health;
CREATE VIEW api.v_system_health AS
SELECT now() AS checked_at,
       (SELECT max(sent_at) FROM core.messages) AS last_message_at,
       (SELECT max(created_at) FROM core.analyses) AS last_analysis_at,
       (SELECT count(*) FROM core.jobs WHERE status = 'queued') AS queued_jobs,
       (SELECT count(*) FROM core.jobs WHERE status = 'running') AS running_jobs,
       (SELECT count(*) FROM core.jobs WHERE status = 'dead') AS dead_jobs,
       (SELECT count(*) FROM core.media md
         WHERE md.kind IN ('voice', 'video', 'video_note', 'audio') AND md.status = 'downloaded'
           AND NOT EXISTS (SELECT 1 FROM core.transcripts t WHERE t.media_id = md.id)) AS untranscribed_media,
       (SELECT count(*) FROM core.review_queue WHERE reviewed_at IS NULL) AS open_reviews,
       (SELECT count(*) FROM core.messages_default) + (SELECT count(*) FROM core.transcripts_default) AS default_partition_rows,
       (SELECT min(last_beat_at) FROM core.heartbeats) AS oldest_heartbeat_at,
       pg_database_size(current_database()) AS db_bytes;
DROP TABLE core.scheduler_runs;
DROP TABLE core.alerts;
DELETE FROM core.config WHERE key LIKE 'alerts.%' OR key LIKE 'schedule.%'
   OR key IN ('retention.video_days', 'retention.video_root', 'retention.protect_paths', 'retention.delete_enabled');
