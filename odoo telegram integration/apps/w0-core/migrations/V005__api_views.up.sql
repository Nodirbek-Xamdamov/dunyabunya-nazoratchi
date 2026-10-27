-- V005 api views: the stable read surface for W3 (CONTRACTS §6 + W3 [CONTRACT] requests, SCHEMA.md §5).
-- Views run with the owner's rights (genesis_migrator), so W3 needs SELECT on api only.

CREATE FUNCTION core.tg_link(chat_id bigint, username text, msg_id bigint) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    RETURN CASE
        WHEN msg_id IS NULL THEN NULL
        WHEN username IS NOT NULL AND username <> '' THEN 'https://t.me/' || username || '/' || msg_id
        WHEN chat_id <= -1000000000000 THEN 'https://t.me/c/' || (-chat_id - 1000000000000) || '/' || msg_id
        ELSE NULL
    END;
COMMENT ON FUNCTION core.tg_link(bigint, text, bigint) IS 'Asl xabarga havola: public username yoki t.me/c/<id>/<msg> (a''zolar uchun)';

-- Telegram ------------------------------------------------------------------------------------------
CREATE VIEW api.v_sources AS
SELECT s.id AS source_id, s.kind, s.title, s.tg_chat_id, s.tg_username, s.enabled, s.is_present,
       s.admins_checked_at, s.reader,
       coalesce((SELECT jsonb_agg(jsonb_build_object('person_id', p.id, 'name', p.display_name,
                                                     'username', p.username, 'tg_user_id', p.tg_user_id) ORDER BY p.id)
                   FROM core.source_members sm JOIN core.people p ON p.id = sm.person_id
                  WHERE sm.source_id = s.id AND sm.member_role = 'manager'), '[]') AS managers,
       coalesce((SELECT jsonb_agg(jsonb_build_object('person_id', p.id, 'name', p.display_name,
                                                     'username', p.username, 'tg_user_id', p.tg_user_id) ORDER BY p.id)
                   FROM core.source_members sm JOIN core.people p ON p.id = sm.person_id
                  WHERE sm.source_id = s.id AND sm.member_role = 'admin'), '[]') AS admins
FROM core.sources s;

CREATE VIEW api.v_messages_public AS
SELECT m.source_id, s.title AS source_title, s.tg_chat_id, s.tg_username, m.tg_message_id, m.sent_at, m.local_day,
       m.sender_tg_id, coalesce(p.display_name, m.sender_name) AS sender_name,
       coalesce(p.role, 'unknown') AS sender_role, m.sender_is_bot, m.kind, m.text,
       m.reply_to_tg_message_id, m.thread_id, m.forward_from, m.edited_at, m.edit_count, m.deleted_at,
       (m.text_purged_at IS NOT NULL) AS text_purged,
       md.id AS media_id, md.kind AS media_kind, md.path AS media_path, md.status AS media_status,
       md.duration_s AS media_duration_s,
       tr.text AS transcript, tr.provider AS transcript_provider, tr.language AS transcript_language,
       core.tg_link(s.tg_chat_id, s.tg_username, m.tg_message_id) AS tg_link
FROM core.messages m
JOIN core.sources s ON s.id = m.source_id
LEFT JOIN core.people p ON p.tg_user_id = m.sender_tg_id
LEFT JOIN core.media md ON md.source_id = m.source_id AND md.tg_message_id = m.tg_message_id
LEFT JOIN LATERAL (SELECT t.text, t.provider, t.language FROM core.transcripts t
                    WHERE t.media_id = md.id AND t.is_primary ORDER BY t.created_at DESC LIMIT 1) tr ON true;

CREATE VIEW api.v_episode_messages AS
SELECT em.episode_id, vm.*
FROM core.episode_messages em
JOIN api.v_messages_public vm
  ON vm.source_id = em.source_id AND vm.tg_message_id = em.tg_message_id AND vm.sent_at = em.sent_at;

CREATE VIEW api.v_episode_participants AS
SELECT em.episode_id, m.sender_tg_id, coalesce(max(p.display_name), max(m.sender_name)) AS name,
       coalesce(max(p.role), 'unknown') AS role, count(*) AS message_count,
       min(m.sent_at) AS first_at, max(m.sent_at) AS last_at
FROM core.episode_messages em
JOIN core.messages m ON m.source_id = em.source_id AND m.tg_message_id = em.tg_message_id AND m.sent_at = em.sent_at
LEFT JOIN core.people p ON p.tg_user_id = m.sender_tg_id
GROUP BY em.episode_id, m.sender_tg_id;

CREATE VIEW api.v_analysis_feed AS
SELECT a.id AS analysis_id, a.episode_id, e.source_id, s.title AS source_title, s.tg_chat_id, s.tg_username,
       e.started_at, e.ended_at, e.local_day, e.message_count, e.relevance,
       a.categories, a.primary_category, a.sentiment, a.severity, a.summary, a.evidence, a.entities,
       a.needs_reply, a.confidence, a.model_id, a.prompt_version, 'AI taxmini'::text AS ai_label,
       EXISTS (SELECT 1 FROM core.review_queue rq
                WHERE rq.item_type = 'analysis' AND rq.item_id = a.id AND rq.reviewed_at IS NULL) AS in_review,
       a.created_at
FROM core.analyses a
JOIN core.episodes e ON e.id = a.episode_id
JOIN core.sources s ON s.id = e.source_id
WHERE a.is_current;

CREATE VIEW api.v_daily_category_counts AS
SELECT e.local_day, e.source_id, s.title AS source_title, c.category,
       count(*) AS episode_count,
       count(*) FILTER (WHERE a.sentiment = 'negative') AS negative_count,
       count(*) FILTER (WHERE a.needs_reply) AS needs_reply_count
FROM core.analyses a
JOIN core.episodes e ON e.id = a.episode_id
JOIN core.sources s ON s.id = e.source_id
CROSS JOIN LATERAL unnest(a.categories) AS c(category)
WHERE a.is_current AND e.relevance = 'yes'
GROUP BY e.local_day, e.source_id, s.title, c.category;

CREATE VIEW api.v_daily_source_counts AS
WITH m AS (
    SELECT m.local_day, m.source_id, count(*) AS messages,
           count(*) FILTER (WHERE coalesce(p.role, 'unknown') <> 'employee' AND NOT m.sender_is_bot) AS client_messages
    FROM core.messages m LEFT JOIN core.people p ON p.tg_user_id = m.sender_tg_id
    GROUP BY m.local_day, m.source_id
), e AS (
    SELECT local_day, source_id,
           count(*) FILTER (WHERE relevance = 'yes') AS related_episodes,
           count(*) FILTER (WHERE relevance = 'no') AS filtered_episodes,
           count(*) FILTER (WHERE relevance = 'unsure') AS unsure_episodes,
           count(*) FILTER (WHERE relevance IS NULL) AS pending_episodes
    FROM core.episodes GROUP BY local_day, source_id
)
SELECT coalesce(m.local_day, e.local_day) AS local_day, coalesce(m.source_id, e.source_id) AS source_id,
       s.title AS source_title,
       coalesce(m.messages, 0) AS messages, coalesce(m.client_messages, 0) AS client_messages,
       coalesce(e.related_episodes, 0) AS related_episodes, coalesce(e.filtered_episodes, 0) AS filtered_episodes,
       coalesce(e.unsure_episodes, 0) AS unsure_episodes, coalesce(e.pending_episodes, 0) AS pending_episodes
FROM m FULL JOIN e ON e.local_day = m.local_day AND e.source_id = m.source_id
JOIN core.sources s ON s.id = coalesce(m.source_id, e.source_id);

CREATE VIEW api.v_response_times AS
SELECT r.id AS response_pair_id, r.source_id, s.title AS source_title, r.local_day,
       r.request_tg_message_id, r.requested_at, r.requester_tg_id, r.deadline_at,
       date_part('epoch', r.deadline_at - r.requested_at) AS sla_s,
       r.status, (r.status = 'answered') AS within_sla, r.answered_at, r.response_s, r.answer_tg_message_id,
       r.responder_tg_id, r.responder_person_id, p.display_name AS responder_name, r.responder_role,
       r.late_answer_at, r.episode_id, r.rule_version, r.computed_at,
       core.tg_link(s.tg_chat_id, s.tg_username, r.request_tg_message_id) AS request_link,
       core.tg_link(s.tg_chat_id, s.tg_username, r.answer_tg_message_id) AS answer_link
FROM core.response_pairs r
JOIN core.sources s ON s.id = r.source_id
LEFT JOIN core.people p ON p.id = r.responder_person_id;

CREATE VIEW api.v_daily_summary AS
SELECT local_day, scope, scope_key, summary, model_id, prompt_version, 'AI taxmini'::text AS ai_label, created_at
FROM core.daily_summaries;

CREATE VIEW api.v_review_queue AS
SELECT rq.id AS review_id, rq.item_type, rq.item_id, rq.reason, rq.confidence, rq.details, rq.status,
       rq.human_decision, rq.reviewed_by, rq.reviewed_at, rq.created_at,
       a.episode_id, e.source_id, s.title AS source_title, a.summary, a.categories, a.sentiment
FROM core.review_queue rq
LEFT JOIN core.analyses a ON rq.item_type = 'analysis' AND a.id = rq.item_id
LEFT JOIN core.episodes e ON e.id = a.episode_id
LEFT JOIN core.sources s ON s.id = e.source_id;

-- system ----------------------------------------------------------------------------------------------
CREATE VIEW api.v_pipeline_status AS
SELECT type, gpu_class, status, count(*) AS jobs,
       min(run_after) FILTER (WHERE status = 'queued') AS oldest_queued_at,
       max(updated_at) AS last_change_at, max(attempts) AS max_attempts_seen
FROM core.jobs
GROUP BY type, gpu_class, status;

CREATE VIEW api.v_heartbeats AS
SELECT component, instance, status, last_beat_at, date_part('epoch', now() - last_beat_at) AS age_s,
       started_at, version, details
FROM core.heartbeats;

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

CREATE VIEW api.v_partition_health AS
SELECT parent.relname AS parent, c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds,
       c.relname LIKE '%\_default' AS is_default, greatest(c.reltuples, 0)::bigint AS est_rows
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_namespace n ON n.oid = parent.relnamespace
WHERE n.nspname = 'core' AND c.relkind IN ('r', 'p');

-- camera ---------------------------------------------------------------------------------------------
CREATE VIEW api.v_attendance_daily AS
SELECT dp.local_day, dp.anon_person_id, ap.label AS person_label, dp.camera_id, c.name AS camera_name,
       dp.first_in_at, dp.last_out_at, dp.office_minutes, dp.room_minutes, dp.away_minutes, dp.session_count,
       dp.uncertain, dp.uncertainty_note, 'AI taxmini'::text AS ai_label, dp.analysis_run, dp.computed_at
FROM core.daily_presence dp
JOIN core.anon_persons ap ON ap.id = dp.anon_person_id
JOIN core.cameras c ON c.id = dp.camera_id;

CREATE VIEW api.v_attendance_today AS
SELECT * FROM api.v_attendance_daily WHERE local_day = core.tashkent_day(now());

CREATE VIEW api.v_presence_sessions AS
SELECT ps.id AS session_id, ps.local_day, ps.camera_id, ps.anon_person_id, ap.label AS person_label, ps.kind,
       ps.started_at, ps.ended_at, ps.minutes, ps.inferred,
       coalesce(ein.inferred, false) AS enter_inferred, coalesce(eout.inferred, false) AS exit_inferred,
       ps.confidence, ps.analysis_run
FROM core.presence_sessions ps
JOIN core.anon_persons ap ON ap.id = ps.anon_person_id
LEFT JOIN core.camera_events ein ON ein.id = ps.enter_event_id
LEFT JOIN core.camera_events eout ON eout.id = ps.exit_event_id;

CREATE VIEW api.v_camera_events AS
SELECT ce.id AS event_id, ce.camera_id, ce.at, ce.local_day, ce.direction, ce.method, ce.inferred, ce.confidence,
       ce.anon_person_id, ap.label AS person_label, ce.track_id, ce.video_segment_id, vs.file AS segment_file,
       ce.segment_offset_s, ce.analysis_run
FROM core.camera_events ce
LEFT JOIN core.anon_persons ap ON ap.id = ce.anon_person_id
LEFT JOIN core.video_segments vs ON vs.id = ce.video_segment_id;

CREATE VIEW api.v_camera_coverage AS
WITH seg AS (
    SELECT camera_id, local_day, count(*) AS segments, sum(duration_s) AS recorded_s,
           min(started_at) AS first_start_at, max(ended_at) AS last_end_at,
           count(*) FILTER (WHERE break_before) AS breaks,
           count(*) FILTER (WHERE end_source = 'mtime') AS unclosed_segments,
           count(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted_segments
    FROM core.video_segments GROUP BY camera_id, local_day
), gap AS (
    SELECT camera_id, local_day, count(*) AS gaps, sum(duration_s) AS gap_s
    FROM core.video_gaps GROUP BY camera_id, local_day
)
SELECT seg.camera_id, c.name AS camera_name, seg.local_day, seg.segments, seg.recorded_s,
       coalesce(gap.gaps, 0) AS gaps, coalesce(gap.gap_s, 0) AS gap_s, seg.breaks, seg.unclosed_segments,
       seg.deleted_segments, seg.first_start_at, seg.last_end_at,
       date_part('epoch', c.record_end - c.record_start) AS window_s,
       round(100 * seg.recorded_s / nullif(date_part('epoch', c.record_end - c.record_start), 0)::numeric, 1) AS coverage_pct
FROM seg
JOIN core.cameras c ON c.id = seg.camera_id
LEFT JOIN gap ON gap.camera_id = seg.camera_id AND gap.local_day = seg.local_day;
