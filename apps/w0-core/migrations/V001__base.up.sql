-- V001 base: shared helpers, config, people, sources, jobs, heartbeats, audit, archive log.
-- Run by migrate.py as genesis_migrator inside one transaction. Times are timestamptz (UTC stored);
-- the business day is Asia/Tashkent (P-07) and comes from core.tashkent_day().

CREATE FUNCTION core.tashkent_day(ts timestamptz) RETURNS date
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    RETURN (ts AT TIME ZONE 'Asia/Tashkent')::date;
COMMENT ON FUNCTION core.tashkent_day(timestamptz) IS 'Hisobot kuni: UTC vaqtdan Asia/Tashkent bo''yicha sana (kun chegarasi 00:00, P-07)';

CREATE FUNCTION core.touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- config -------------------------------------------------------------------------------------
CREATE TABLE core.config (
    key          text PRIMARY KEY,
    value        jsonb NOT NULL,
    description  text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   text NOT NULL DEFAULT current_user
);
COMMENT ON TABLE core.config IS 'Bazadagi ish sozlamalari (W0 yozadi). Fayl sozlamalari config.toml da';
CREATE TRIGGER config_touch BEFORE UPDATE ON core.config FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

INSERT INTO core.config (key, value, description) VALUES
  ('retention.unrelated_text_hours', '72', 'P-05 / Q15 (vaqtincha, foydalanuvchi tasdiqlaydi): relevance=no episode matni shuncha soatdan keyin o''chadi'),
  ('partitions.months_ahead', '3', 'messages/transcripts: joriy oydan keyin oldindan yaratiladigan oylar soni'),
  ('archive.keep_months', '12', 'D-13: shundan eski oy partitionlari G: ga arxivlanadi (archive_month.py, W0-08)'),
  ('response.sla_seconds', '300', 'So''rov -> javob muddati (prototipdagi 5 daqiqa qoidasi, Q7)'),
  ('camera.analysis_fps_default', '1', 'D-20: tahlilda yozuvdan siyraklashtirish (1/2/3/6)'),
  ('report.tg_daily_time', '"00:10"', 'Telegram Excel yuborish vaqti (D-05)'),
  ('report.camera_daily_time', '"09:00"', 'Kamera hisoboti vaqti (D-05)');

-- people ---------------------------------------------------------------------------------------
CREATE TABLE core.people (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tg_user_id    bigint UNIQUE,
    username      text,
    display_name  text,
    role          text NOT NULL DEFAULT 'unknown' CHECK (role IN ('employee', 'client', 'unknown')),
    is_manager    boolean NOT NULL DEFAULT false,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.people IS 'Odamlar reestri; hodimlar qo''lda to''ldiriladi (Q5). W1/W2 faqat o''qiydi';
COMMENT ON COLUMN core.people.tg_user_id IS 'Telegram user id; messages.sender_tg_id bilan bog''lanadi (FK yo''q)';
CREATE TRIGGER people_touch BEFORE UPDATE ON core.people FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- sources --------------------------------------------------------------------------------------
CREATE TABLE core.sources (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind               text NOT NULL CHECK (kind IN ('group', 'supergroup', 'channel', 'dm')),
    tg_chat_id         bigint NOT NULL UNIQUE,
    tg_username        text,
    title              text NOT NULL,
    enabled            boolean NOT NULL DEFAULT true,
    reader             text,
    is_present         boolean NOT NULL DEFAULT true,
    admins_checked_at  timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.sources IS 'Manba: guruh (keyin DM, W5). W1 yozadi';
COMMENT ON COLUMN core.sources.tg_chat_id IS 'Telegram chat id (supergroup: -100...). Asl xabarga havola uchun';
COMMENT ON COLUMN core.sources.tg_username IS 'Public username (bo''lsa): havola t.me/<username>/<msg_id>';
COMMENT ON COLUMN core.sources.reader IS 'Qaysi akkaunt/bot o''qiydi (W1-06 reestri)';
COMMENT ON COLUMN core.sources.is_present IS 'O''quvchi akkaunt guruhda bormi';
CREATE TRIGGER sources_touch BEFORE UPDATE ON core.sources FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

CREATE TABLE core.source_members (
    source_id    bigint NOT NULL REFERENCES core.sources (id) ON DELETE CASCADE,
    person_id    bigint NOT NULL REFERENCES core.people (id) ON DELETE CASCADE,
    member_role  text NOT NULL CHECK (member_role IN ('manager', 'admin')),
    origin       text NOT NULL DEFAULT 'telegram' CHECK (origin IN ('telegram', 'manual')),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, person_id, member_role)
);
COMMENT ON TABLE core.source_members IS 'Guruh menejeri/adminlari (W3 [CONTRACT] 3). Adminlarni W1 aniqlaydi, menejer qo''lda';

-- jobs (queue; behaviour in W0-05) ---------------------------------------------------------------
CREATE TABLE core.jobs (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type             text NOT NULL,
    payload          jsonb NOT NULL DEFAULT '{}',
    status           text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed', 'dead')),
    gpu_class        text NOT NULL DEFAULT 'none' CHECK (gpu_class IN ('none', 'llm', 'vision')),
    priority         smallint NOT NULL DEFAULT 0,
    attempts         integer NOT NULL DEFAULT 0,
    max_attempts     integer NOT NULL DEFAULT 5,
    run_after        timestamptz NOT NULL DEFAULT now(),
    idempotency_key  text,
    locked_by        text,
    locked_at        timestamptz,
    heartbeat_at     timestamptz,
    last_error       text,
    created_by       text NOT NULL DEFAULT current_user,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);
COMMENT ON TABLE core.jobs IS 'Ish navbati (CONTRACTS §2). SKIP LOCKED mantiqi W0-05 da (shared/queue)';
CREATE UNIQUE INDEX jobs_idempotency_key ON core.jobs (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX jobs_ready ON core.jobs (gpu_class, priority DESC, run_after) WHERE status = 'queued';
CREATE INDEX jobs_type_status ON core.jobs (type, status);
CREATE TRIGGER jobs_touch BEFORE UPDATE ON core.jobs FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- heartbeats ---------------------------------------------------------------------------------------
CREATE TABLE core.heartbeats (
    component     text NOT NULL,
    instance      text NOT NULL DEFAULT 'main',
    last_beat_at  timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    status        text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'degraded', 'error', 'stopped')),
    version       text,
    details       jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (component, instance)
);
COMMENT ON TABLE core.heartbeats IS 'Jonlilik: har servis o''z qatorini yangilaydi (W0-08 signali)';

-- audit_log ------------------------------------------------------------------------------------------
CREATE TABLE core.audit_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    actor        text NOT NULL,
    action       text NOT NULL,
    object_type  text,
    object_id    text,
    details      jsonb NOT NULL DEFAULT '{}',
    client_addr  inet
);
COMMENT ON TABLE core.audit_log IS 'Kim nimani ko''rdi/o''zgartirdi. W3 faqat qo''shadi (INSERT)';
CREATE INDEX audit_log_at ON core.audit_log (at);

-- archive_log ------------------------------------------------------------------------------------------
CREATE TABLE core.archive_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent       text NOT NULL,
    partition    text NOT NULL,
    month        date NOT NULL,
    row_count    bigint NOT NULL,
    file_path    text NOT NULL,
    file_bytes   bigint NOT NULL,
    sha256       text NOT NULL,
    verified     boolean NOT NULL DEFAULT false,
    dropped_at   timestamptz,
    archived_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (parent, month)
);
COMMENT ON TABLE core.archive_log IS 'Oylik arxiv jurnali (D-13): partition -> siqilgan fayl (G:) -> o''chirish';
