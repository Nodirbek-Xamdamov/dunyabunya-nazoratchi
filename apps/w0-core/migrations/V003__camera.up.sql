-- V003 camera (D-11, D-17, D-20, D-24). Anonymous staff ("Hodim N"), no names (D-06).
-- Embeddings stay on the server (rule 6): bytea / real[] here, no pgvector (P-06).

CREATE TABLE core.cameras (
    id              text PRIMARY KEY,
    name            text NOT NULL,
    location        text,
    enabled         boolean NOT NULL DEFAULT true,
    rtsp_secret     text NOT NULL,
    stream_channel  integer NOT NULL,
    codec           text,
    width           integer,
    height          integer,
    stream_fps      numeric(6, 2),
    record_start    time NOT NULL DEFAULT '07:00',
    record_end      time NOT NULL DEFAULT '22:00',
    timezone        text NOT NULL DEFAULT 'Asia/Tashkent',
    analysis_fps    numeric(4, 2) NOT NULL DEFAULT 1 CHECK (analysis_fps > 0),
    door_zone       jsonb,
    zone_file       text,
    zone_status     text NOT NULL DEFAULT 'provisional' CHECK (zone_status IN ('provisional', 'confirmed')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.cameras IS 'Kamera reestri (W0 yozadi, W4 o''qiydi). Sozlama kamerada o''zgartirilmaydi (D-20)';
COMMENT ON COLUMN core.cameras.rtsp_secret IS 'secrets/ ichidagi fayl nomi. RTSP manzil/parol bazaga yozilmaydi';
COMMENT ON COLUMN core.cameras.stream_channel IS 'Yoziladigan oqim (D-20: 102, o''z holida)';
COMMENT ON COLUMN core.cameras.record_start IS 'Yozuv oynasi, mahalliy vaqt (D-24: 07:00-22:00)';
COMMENT ON COLUMN core.cameras.analysis_fps IS 'Tahlil fps: yozuvdan siyraklashtiriladi (D-20: 1/2/3/6, W4-04 tanlaydi)';
COMMENT ON COLUMN core.cameras.door_zone IS 'Eshik zonasi (zone_config.json mazmuni, D-24). NULL = zone_file dan';
CREATE TRIGGER cameras_touch BEFORE UPDATE ON core.cameras FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

INSERT INTO core.cameras (id, name, location, rtsp_secret, stream_channel, codec, width, height, stream_fps,
                          record_start, record_end, analysis_fps, zone_file, zone_status)
VALUES ('camera1', 'koridor_2', 'ichkaridan eshikka qaraydi (D-24)', 'camera1.txt', 102, 'hevc', 640, 360, 20,
        '07:00', '22:00', 1, 'apps/w4-camera/intake/zone_config.json', 'provisional');

-- video ---------------------------------------------------------------------------------------------------
CREATE TABLE core.video_segments (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camera_id      text NOT NULL REFERENCES core.cameras (id),
    local_day      date NOT NULL,
    file           text NOT NULL,
    path           text,
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz NOT NULL,
    duration_s     numeric(10, 3) NOT NULL,
    bytes          bigint,
    session        text,
    seq            integer,
    break_before   boolean NOT NULL DEFAULT false,
    gap_before_s   numeric(10, 3),
    end_source     text CHECK (end_source IN ('list', 'mtime')),
    closed_reason  text CHECK (closed_reason IN ('segment', 'stop', 'stall', 'ffmpeg_exit', 'recovered')),
    codec          text,
    width          integer,
    height         integer,
    measured_fps   numeric(6, 2),
    manifest_v     smallint,
    is_keep        boolean NOT NULL DEFAULT false,
    deleted_at     timestamptz,
    imported_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (camera_id, file)
);
COMMENT ON TABLE core.video_segments IS 'Yozilgan video bo''laklari (W4). Manba: <video_root>\<kamera>\<kun>\manifest.jsonl (v1), core.import_manifest_record()';
COMMENT ON COLUMN core.video_segments.local_day IS 'Manifest "day" (mahalliy kun papkasi)';
COMMENT ON COLUMN core.video_segments.end_source IS 'list = ffmpeg ro''yxatidan aniq; mtime = bo''lak yopilmagan (uzilish)';
COMMENT ON COLUMN core.video_segments.break_before IS 'Uzilish belgisi: oldingi bo''lak bilan orada uzilish bor';
COMMENT ON COLUMN core.video_segments.measured_fps IS 'O''lchangan fps (W4 probe); manifest v1 da yo''q, NULL bo''lishi mumkin';
COMMENT ON COLUMN core.video_segments.deleted_at IS 'Aylanma bufer faylni o''chirgan vaqt (qator qoladi)';
CREATE INDEX video_segments_time ON core.video_segments (camera_id, started_at);

CREATE TABLE core.video_gaps (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camera_id   text NOT NULL REFERENCES core.cameras (id),
    local_day   date NOT NULL,
    from_at     timestamptz NOT NULL,
    to_at       timestamptz NOT NULL,
    duration_s  numeric(10, 3) NOT NULL,
    reason      text,
    UNIQUE (camera_id, from_at)
);
COMMENT ON TABLE core.video_gaps IS 'Yozuv oynasi ichidagi uzilishlar (manifest type=gap)';

-- persons, events, presence -------------------------------------------------------------------------
CREATE TABLE core.anon_persons (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label            text NOT NULL UNIQUE,
    scope            text NOT NULL DEFAULT 'permanent' CHECK (scope IN ('daily', 'permanent')),
    valid_day        date,
    embedding        bytea,
    embedding_f4     real[],
    embedding_model  text,
    first_seen_at    timestamptz,
    last_seen_at     timestamptz,
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK ((scope = 'daily') = (valid_day IS NOT NULL))
);
COMMENT ON TABLE core.anon_persons IS '"Hodim N" (D-06): ismsiz. Embedding serverdan chiqmaydi (qoida 6), pgvector yo''q (P-06)';
COMMENT ON COLUMN core.anon_persons.embedding IS 'Ko''rinish embeddingi, xom bayt (float32 LE)';
COMMENT ON COLUMN core.anon_persons.embedding_f4 IS 'Xuddi shu embedding real[] ko''rinishida (ixtiyoriy)';

CREATE TABLE core.camera_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camera_id         text NOT NULL REFERENCES core.cameras (id),
    at                timestamptz NOT NULL,
    local_day         date GENERATED ALWAYS AS (core.tashkent_day(at)) STORED,
    direction         text NOT NULL CHECK (direction IN ('in', 'out')),
    method            text NOT NULL CHECK (method IN ('zone_appear', 'zone_disappear', 'direction', 'manual', 'recovered')),
    track_id          text,
    anon_person_id    bigint REFERENCES core.anon_persons (id),
    confidence        real,
    inferred          boolean NOT NULL DEFAULT false,
    video_segment_id  bigint REFERENCES core.video_segments (id),
    segment_offset_s  numeric(10, 3),
    analysis_run      text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (analysis_run, camera_id, track_id, at, direction)
);
COMMENT ON TABLE core.camera_events IS 'Kirdi/chiqdi (W4). D-24: ostona ko''rinmaydi, hodisa eshik zonasida trek paydo bo''lishi/yo''qolishi va yo''nalishidan';
COMMENT ON COLUMN core.camera_events.inferred IS 'Tiklangan (ko''rilmagan, mantiqan chiqarilgan) hodisa';
COMMENT ON COLUMN core.camera_events.analysis_run IS 'Tahlil yurishi id (qayta hisoblashda idempotent)';
CREATE INDEX camera_events_time ON core.camera_events (camera_id, at);

CREATE TABLE core.presence_sessions (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camera_id       text NOT NULL REFERENCES core.cameras (id),
    anon_person_id  bigint NOT NULL REFERENCES core.anon_persons (id),
    local_day       date NOT NULL,
    kind            text NOT NULL DEFAULT 'room' CHECK (kind IN ('room')),
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    minutes         numeric GENERATED ALWAYS AS (date_part('epoch', ended_at - started_at) / 60) STORED,
    enter_event_id  bigint REFERENCES core.camera_events (id),
    exit_event_id   bigint REFERENCES core.camera_events (id),
    inferred        boolean NOT NULL DEFAULT false,
    confidence      real,
    analysis_run    text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_run, anon_person_id, started_at)
);
COMMENT ON TABLE core.presence_sessions IS 'Xonada bo''lish oralig''i (kirdi -> chiqdi). D-24: xona ichidagi chiqishlar hozircha "xonada" (W4-07 ochiq)';
COMMENT ON COLUMN core.presence_sessions.ended_at IS 'NULL = kun oxirigacha chiqish ko''rilmagan';

CREATE TABLE core.daily_presence (
    local_day         date NOT NULL,
    anon_person_id    bigint NOT NULL REFERENCES core.anon_persons (id),
    camera_id         text NOT NULL REFERENCES core.cameras (id),
    first_in_at       timestamptz,
    last_out_at       timestamptz,
    office_minutes    numeric,
    room_minutes      numeric,
    away_minutes      numeric,
    session_count     integer NOT NULL DEFAULT 0,
    uncertain         boolean NOT NULL DEFAULT false,
    uncertainty_note  text,
    analysis_run      text NOT NULL,
    computed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (local_day, anon_person_id, camera_id)
);
COMMENT ON TABLE core.daily_presence IS 'Kunlik jami (D-11): ishxonada (birinchi kirish -> oxirgi chiqish) va xonada alohida';
COMMENT ON COLUMN core.daily_presence.office_minutes IS 'Ishxonada: first_in_at -> last_out_at';
COMMENT ON COLUMN core.daily_presence.room_minutes IS 'Xonada: presence_sessions yig''indisi';
COMMENT ON COLUMN core.daily_presence.away_minutes IS 'Xonadan tashqarida (hojatxona, ko''cha): office - room';

-- manifest import (W4-02 [CONTRACT]) -------------------------------------------------------------------
CREATE FUNCTION core.import_manifest_record(rec jsonb, video_root text DEFAULT NULL) RETURNS text
    LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce((rec ->> 'v')::int, 0) <> 1 THEN
        RAISE EXCEPTION 'unsupported manifest version: %', rec ->> 'v';
    END IF;
    IF rec ->> 'type' = 'segment' THEN
        INSERT INTO core.video_segments AS s (camera_id, local_day, file, path, started_at, ended_at, duration_s,
               bytes, session, seq, break_before, gap_before_s, end_source, closed_reason, manifest_v)
        VALUES (rec ->> 'camera', (rec ->> 'day')::date, rec ->> 'file',
                CASE WHEN video_root IS NULL THEN NULL
                     ELSE video_root || '\' || (rec ->> 'camera') || '\' || (rec ->> 'day') || '\' || (rec ->> 'file') END,
                (rec ->> 'start_utc')::timestamptz, (rec ->> 'end_utc')::timestamptz, (rec ->> 'duration_s')::numeric,
                (rec ->> 'bytes')::bigint, rec ->> 'session', (rec ->> 'seq')::int,
                coalesce((rec ->> 'break_before')::boolean, false), (rec ->> 'gap_before_s')::numeric,
                rec ->> 'end_source', rec ->> 'closed', 1)
        ON CONFLICT (camera_id, file) DO UPDATE SET
            ended_at = EXCLUDED.ended_at, duration_s = EXCLUDED.duration_s, bytes = EXCLUDED.bytes,
            end_source = EXCLUDED.end_source, closed_reason = EXCLUDED.closed_reason,
            path = coalesce(EXCLUDED.path, s.path), imported_at = now();
        RETURN 'segment';
    ELSIF rec ->> 'type' = 'gap' THEN
        INSERT INTO core.video_gaps (camera_id, local_day, from_at, to_at, duration_s, reason)
        VALUES (rec ->> 'camera', (rec ->> 'day')::date, (rec ->> 'from_utc')::timestamptz,
                (rec ->> 'to_utc')::timestamptz, (rec ->> 'duration_s')::numeric, rec ->> 'reason')
        ON CONFLICT (camera_id, from_at) DO UPDATE SET
            to_at = EXCLUDED.to_at, duration_s = EXCLUDED.duration_s, reason = EXCLUDED.reason;
        RETURN 'gap';
    END IF;
    RETURN 'skipped';
END $$;
COMMENT ON FUNCTION core.import_manifest_record(jsonb, text) IS 'W4 manifest.jsonl (v1) bitta qatorini video_segments/video_gaps ga idempotent yozadi';
