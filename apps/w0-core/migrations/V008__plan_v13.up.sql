-- V008 plan v1.3 (D-27, D-29, D-31, D-32, D-33). Only what the schema was missing:
--   core.categories           the 7 Telegram sections (D-33), no "Boshqa"
--   anon_persons.staff_no      4 permanent staff "Hodim 1..4" (D-31); guests get no row at all
--   core.camera_event_frames   time-stamped frame per entry/exit event (D-31, W4-05)
--   api.v_categories, api.v_staff_day   for W3 (W3-03, W3-04; D-32 formulas)
-- D-29 indexes already exist: messages_source_time (source_id = chat, sent_at), camera_events_time
-- (camera_id, at), video_segments_time (camera_id, started_at); messages/transcripts are monthly partitions (V002/V004).

UPDATE core.config
   SET description = 'D-27 (P-05 tasdiqlangan): relevance=no episode matni shuncha soatdan keyin o''chadi; muddat shu sozlamada'
 WHERE key = 'retention.unrelated_text_hours';

CREATE TABLE core.categories (
    code         text PRIMARY KEY CHECK (code ~ '^[a-z_]+$'),
    name         text NOT NULL UNIQUE,
    sort         smallint NOT NULL UNIQUE,
    active       boolean NOT NULL DEFAULT true,
    description  text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.categories IS 'D-33: Telegram tasnifining 7 bo''limi ("Boshqa" yo''q). Aloqasiz xabar hech bir bo''limga kirmaydi; ishonchsiz -> review_queue';
COMMENT ON COLUMN core.categories.code IS 'analyses.categories / primary_category / daily_stats.category shu kodlarni yozadi (W2)';
COMMENT ON COLUMN core.categories.name IS 'Foydalanuvchi bergan nom, aynan shunday ko''rsatiladi';
INSERT INTO core.categories (code, name, sort, description) VALUES
  ('delivery',         'Dastavka va Logistika',          1, 'Yetkazib berish, muddat, mashina, manzil, tushirish'),
  ('quality',          'Mahsulot Sifati va Nuqsonlar',   2, 'Sifat, nuqson, singan/buzilgan tovar'),
  ('completeness',     'Komplektatsiya va Hajm',         3, 'Noto''g''ri yoki yetishmagan tovar, miqdor, hajm'),
  ('staff_service',    'Xodimlar va Xizmat Ko''rsatish', 4, 'Hodimlar muomalasi, javob berish, xizmat'),
  ('price_payment',    'Narx va To''lovlar',             5, 'Narx, to''lov, hisob-kitob, chegirma'),
  ('returns_warranty', 'Qaytarish va Kafolat',           6, 'Qaytarish, almashtirish, kafolat'),
  ('positive',         'Ijobiy Fikrlar',                 7, 'Minnatdorchilik, maqtov');

ALTER TABLE core.anon_persons ADD COLUMN staff_no smallint UNIQUE CHECK (staff_no BETWEEN 1 AND 999);
ALTER TABLE core.anon_persons ADD CONSTRAINT anon_persons_staff_permanent CHECK (staff_no IS NULL OR scope = 'permanent');
COMMENT ON COLUMN core.anon_persons.staff_no IS 'D-31: asosiy hodim raqami ("Hodim N", doimiy, ismsiz). NULL = asosiy hodim emas; mehmonlar hisobga olinmaydi';
INSERT INTO core.anon_persons (label, scope, note)
SELECT 'Hodim ' || n, 'permanent', 'V008 seed (D-31)' FROM generate_series(1, 4) AS n
ON CONFLICT (label) DO NOTHING;
UPDATE core.anon_persons SET staff_no = substr(label, 7)::smallint
 WHERE scope = 'permanent' AND label IN ('Hodim 1', 'Hodim 2', 'Hodim 3', 'Hodim 4');

CREATE TABLE core.camera_event_frames (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id    bigint NOT NULL REFERENCES core.camera_events(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('in', 'out')),
    frame_at    timestamptz NOT NULL,
    local_day   date GENERATED ALWAYS AS (core.tashkent_day(frame_at)) STORED,
    file_path   text NOT NULL,
    width       integer,
    height      integer,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id, frame_at)
);
COMMENT ON TABLE core.camera_event_frames IS 'D-31: kirgan/chiqqan paytdagi kadr (W4-05 yozadi). Dashboardda faqat ruxsatli rollar ko''radi (W3-05). Rasm serverdan chiqmaydi';
COMMENT ON COLUMN core.camera_event_frames.kind IS 'kirdi = in, chiqdi = out (camera_events.direction bilan bir xil)';
COMMENT ON COLUMN core.camera_event_frames.frame_at IS 'Kadrning o''z vaqti (UTC); hodisa vaqtidan bir necha soniya farq qilishi mumkin';
COMMENT ON COLUMN core.camera_event_frames.file_path IS 'D-29: E:\data\camera_events\YYYY\MM\DD\<kamera>\... (to''liq yo''l)';
CREATE INDEX camera_event_frames_day ON core.camera_event_frames (local_day);

CREATE VIEW api.v_categories AS
SELECT code, name, sort, active, description FROM core.categories;

-- D-32: ishda (office) = first entry -> last exit; xonada (room) = sum of presence sessions, i.e. office minus every
-- time out of the room (toilet, lunch, anything); exits = gaps between consecutive sessions of the same run.
CREATE VIEW api.v_staff_day AS
WITH s AS (
    SELECT ps.local_day, ps.anon_person_id, ps.camera_id, ps.analysis_run, ps.ended_at,
           lead(ps.started_at) OVER (PARTITION BY ps.local_day, ps.anon_person_id, ps.camera_id, ps.analysis_run
                                     ORDER BY ps.started_at) AS next_started_at
    FROM core.presence_sessions ps
), exits AS (
    SELECT local_day, anon_person_id, camera_id, analysis_run, count(*) AS exit_count,
           jsonb_agg(jsonb_build_object('from', ended_at, 'to', next_started_at,
                     'minutes', round((extract(epoch FROM next_started_at - ended_at) / 60)::numeric, 1))
                     ORDER BY ended_at) AS exits
    FROM s
    WHERE ended_at IS NOT NULL AND next_started_at IS NOT NULL
    GROUP BY local_day, anon_person_id, camera_id, analysis_run
), frames AS (
    SELECT ce.local_day, ce.anon_person_id, ce.camera_id, ce.analysis_run,
           jsonb_agg(jsonb_build_object('event_id', ce.id, 'event_at', ce.at, 'kind', f.kind, 'frame_at', f.frame_at,
                                        'path', f.file_path, 'inferred', ce.inferred)
                     ORDER BY f.frame_at) AS frames
    FROM core.camera_events ce
    JOIN core.camera_event_frames f ON f.event_id = ce.id
    WHERE ce.anon_person_id IS NOT NULL
    GROUP BY ce.local_day, ce.anon_person_id, ce.camera_id, ce.analysis_run
)
SELECT dp.local_day, dp.anon_person_id, ap.staff_no, ap.label AS person_label, dp.camera_id, c.name AS camera_name,
       dp.first_in_at, dp.last_out_at, dp.office_minutes, dp.room_minutes, dp.away_minutes,
       coalesce(e.exit_count, 0) AS exit_count, coalesce(e.exits, '[]'::jsonb) AS exits,
       (SELECT f.file_path FROM core.camera_events ce JOIN core.camera_event_frames f ON f.event_id = ce.id
         WHERE ce.anon_person_id = dp.anon_person_id AND ce.camera_id = dp.camera_id
           AND ce.analysis_run = dp.analysis_run AND ce.direction = 'in' AND ce.at = dp.first_in_at
         ORDER BY f.frame_at LIMIT 1) AS first_in_frame,
       (SELECT f.file_path FROM core.camera_events ce JOIN core.camera_event_frames f ON f.event_id = ce.id
         WHERE ce.anon_person_id = dp.anon_person_id AND ce.camera_id = dp.camera_id
           AND ce.analysis_run = dp.analysis_run AND ce.direction = 'out' AND ce.at = dp.last_out_at
         ORDER BY f.frame_at LIMIT 1) AS last_out_frame,
       coalesce(fr.frames, '[]'::jsonb) AS frames,
       dp.session_count, dp.uncertain, dp.uncertainty_note, 'AI taxmini'::text AS ai_label, dp.analysis_run, dp.computed_at
FROM core.daily_presence dp
JOIN core.anon_persons ap ON ap.id = dp.anon_person_id
JOIN core.cameras c ON c.id = dp.camera_id
LEFT JOIN exits e ON (e.local_day, e.anon_person_id, e.camera_id, e.analysis_run)
                   = (dp.local_day, dp.anon_person_id, dp.camera_id, dp.analysis_run)
LEFT JOIN frames fr ON (fr.local_day, fr.anon_person_id, fr.camera_id, fr.analysis_run)
                     = (dp.local_day, dp.anon_person_id, dp.camera_id, dp.analysis_run)
WHERE ap.staff_no IS NOT NULL;
COMMENT ON VIEW api.v_staff_day IS 'W3-04 (D-31, D-32): kun bo''yicha har asosiy hodimning keldi (first_in_at), ketdi (last_out_at), ishda (office_minutes), xonada (room_minutes), chiqish oraliqlari (exits) va kadr yo''llari';
