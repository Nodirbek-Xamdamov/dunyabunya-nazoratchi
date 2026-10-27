-- V010 W0-08: health signals (core.alerts, W3-07 reporter sends them), scheduler run journal, retention settings
-- (D-27, D-13, D-29), schedule settings (D-30: camera batch inside the camera window). Retention starts as DRY-RUN:
-- retention.delete_enabled = false until the user approves the first list.

INSERT INTO core.config (key, value, description) VALUES
  ('alerts.ingest_silence_minutes', '10', 'W1 tinglovchisining yurak urishi shuncha daqiqa yangilanmasa signal (faqat tinglovchi bir marta ishga tushgan bo''lsa)'),
  ('alerts.no_messages_minutes', '180', 'Faol soatlarda shuncha daqiqa birorta ham guruh xabari kelmasa ogohlantirish'),
  ('alerts.no_messages_hours', '"09:00-20:00"', 'no_messages tekshiriladigan soatlar (Asia/Tashkent)'),
  ('alerts.queue_max_ready', '500', 'Hozir olinishi mumkin bo''lgan (gate ruxsat bergan) navbatdagi job soni chegarasi'),
  ('alerts.queue_max_age_minutes', '60', 'Olinishi mumkin bo''lgan eng eski job shuncha daqiqadan beri kutsa signal'),
  ('alerts.dead_jobs_max', '0', 'dead joblar soni shundan oshsa signal'),
  ('alerts.gpu_gate_stale_minutes', '5', 'Gate e''loni yoki yurak urishi shuncha daqiqa eski bo''lsa signal'),
  ('alerts.disk_warn_pct', '80', 'Disk (E:) to''lishi: ogohlantirish'),
  ('alerts.disk_crit_pct', '90', 'Disk (E:) to''lishi: jiddiy'),
  ('alerts.disk_paths', '["E:\\"]', 'Kuzatiladigan disklar'),
  ('alerts.recorder_stale_minutes', '5', 'W4 yozuvchisi yurak urishi (fayl) yozuv oynasida shuncha daqiqa eski bo''lsa signal'),
  ('alerts.recorder_heartbeat_file', '"{root}\\var\\w4-camera\\recorder_heartbeat.json"', 'W4 yozuvchisi yurak urishi fayli'),
  ('schedule.daily_close_time', '"00:05"', 'Kechagi kun uchun daily_close joblari (W2 va W3, alohida target_role)'),
  ('schedule.camera_batch_time', '"22:10"', 'D-24/D-30: shu kungi camera_day_batch navbatga (kamera oynasi ichida bo''lishi shart)'),
  ('schedule.partitions_time', '"01:00"', 'ensure_month_partitions kunlik'),
  ('schedule.retention_time', '"03:30"', 'Retention (video bo''laklari, aloqasiz matn) kunlik'),
  ('schedule.start_day', '"2026-10-27"', 'Shu kundan oldingi kunlar uchun daily_close/camera_day_batch qo''yilmaydi (scheduler o''rnatilgan kun; orqaga to''ldirish qo''lda)'),
  ('retention.video_days', '14', 'D-12/D-29: video bo''laklari aylanma buferi, kun papkasi bo''yicha'),
  ('retention.video_root', '"E:\\data\\video"', 'Faqat shu papka ichidagi <kamera>\\<YYYY-MM-DD> papkalari ko''riladi'),
  ('retention.protect_paths', '["E:\\Kamera raw data", "E:\\data\\video_keep", "F:\\genesis\\keep"]', 'HIMOYA: bu papkalar va ichidagilar hech qachon o''chirilmaydi (eski loyiha xom ma''lumoti, haqiqat/baholash to''plamlari)'),
  ('retention.delete_enabled', 'false', 'false = faqat dry-run (ro''yxat). Foydalanuvchi birinchi ro''yxatni tasdiqlagach true');

CREATE TABLE core.alerts (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key           text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('ingest_silence', 'no_messages', 'queue_backlog', 'dead_jobs', 'gpu_gate',
                                                'disk_space', 'recorder_heartbeat', 'scheduler')),
    severity      text NOT NULL CHECK (severity IN ('warning', 'critical')),
    message       text NOT NULL,
    details       jsonb NOT NULL DEFAULT '{}',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    resolved_at   timestamptz,
    notified_at   timestamptz,
    notified_by   text
);
COMMENT ON TABLE core.alerts IS 'W0-08 signallari (scheduler har daqiqa yozadi/yopadi). Telegramga W3-07 reporter yuboradi va notified_at ni belgilaydi';
COMMENT ON COLUMN core.alerts.key IS 'Bir xil muammo uchun bitta ochiq qator (masalan disk:E:, ingest_silence:w1_ingest/main)';
COMMENT ON COLUMN core.alerts.notified_at IS 'W3-07: reporter yuborgan vaqt (W3 faqat shu ikki ustunni yangilaydi)';
CREATE UNIQUE INDEX alerts_one_open ON core.alerts (key) WHERE resolved_at IS NULL;
CREATE INDEX alerts_recent ON core.alerts (last_seen_at);

CREATE TABLE core.scheduler_runs (
    task        text NOT NULL,
    run_key     text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    ok          boolean,
    result      jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (task, run_key)
);
COMMENT ON TABLE core.scheduler_runs IS 'W0-08 scheduler: har vazifa kuniga bir marta (run_key = kun); o''tkazib yuborilgani keyingi ishga tushishda bajariladi';

CREATE VIEW api.v_alerts AS
SELECT id AS alert_id, key, kind, severity, message, details, first_seen_at, last_seen_at, resolved_at,
       resolved_at IS NULL AS is_open, notified_at, notified_by
FROM core.alerts
WHERE resolved_at IS NULL OR resolved_at > now() - interval '7 days';

CREATE OR REPLACE VIEW api.v_system_health AS
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
       pg_database_size(current_database()) AS db_bytes,
       (SELECT count(*) FROM core.alerts WHERE resolved_at IS NULL) AS open_alerts,
       (SELECT count(*) FROM core.alerts WHERE resolved_at IS NULL AND severity = 'critical') AS critical_alerts,
       (SELECT max(last_beat_at) FROM core.heartbeats WHERE component = 'w0_scheduler') AS scheduler_beat_at;
