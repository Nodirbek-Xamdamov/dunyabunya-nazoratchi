-- V002 Telegram + dashboard tables. messages and transcripts are partitioned by month (D-13);
-- monthly partitions are created by core.ensure_month_partitions() (V004). Until then rows land in
-- the DEFAULT partition (never lost; SCHEMA.md §4).

-- messages (partitioned) -----------------------------------------------------------------------
CREATE TABLE core.messages (
    source_id               bigint NOT NULL REFERENCES core.sources (id),
    tg_message_id           bigint NOT NULL,
    sent_at                 timestamptz NOT NULL,
    local_day               date GENERATED ALWAYS AS (core.tashkent_day(sent_at)) STORED,
    sender_tg_id            bigint,
    sender_name             text,
    sender_is_bot           boolean NOT NULL DEFAULT false,
    kind                    text NOT NULL DEFAULT 'text'
                            CHECK (kind IN ('text', 'voice', 'video', 'video_note', 'photo', 'document', 'audio',
                                            'sticker', 'service', 'other')),
    text                    text,
    reply_to_tg_message_id  bigint,
    thread_id               bigint,
    forward_from            text,
    edited_at               timestamptz,
    edit_count              integer NOT NULL DEFAULT 0,
    deleted_at              timestamptz,
    text_purged_at          timestamptz,
    raw                     jsonb,
    ingested_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, tg_message_id, sent_at)
) PARTITION BY RANGE (sent_at);
COMMENT ON TABLE core.messages IS 'Xom xabar (W1). Oylik partition, kalit (source_id, tg_message_id, sent_at) idempotent';
COMMENT ON COLUMN core.messages.sent_at IS 'Telegram date (UTC). Xabar uchun o''zgarmaydi: partition kaliti';
COMMENT ON COLUMN core.messages.local_day IS 'Hisobot kuni (Asia/Tashkent)';
COMMENT ON COLUMN core.messages.text IS 'Matn yoki caption. relevance=no episode muddati o''tsa NULL qilinadi (P-05)';
COMMENT ON COLUMN core.messages.text_purged_at IS 'Matn qachon o''chirilgan (purge_unrelated_text)';
COMMENT ON COLUMN core.messages.raw IS 'Ixtiyoriy: Telegram obyektidan kerakli qism (sirsiz)';
CREATE TABLE core.messages_default PARTITION OF core.messages DEFAULT;
CREATE INDEX messages_source_time ON core.messages (source_id, sent_at);
CREATE INDEX messages_sender_time ON core.messages (sender_tg_id, sent_at);
CREATE INDEX messages_local_day ON core.messages (local_day);
CREATE INDEX messages_sent_at ON core.messages (sent_at);

-- media ------------------------------------------------------------------------------------------------
CREATE TABLE core.media (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id          bigint NOT NULL REFERENCES core.sources (id),
    tg_message_id      bigint NOT NULL,
    message_sent_at    timestamptz NOT NULL,
    kind               text NOT NULL CHECK (kind IN ('voice', 'video', 'video_note', 'photo', 'document', 'audio',
                                                     'sticker', 'other')),
    status             text NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'downloaded', 'too_large', 'failed', 'skipped')),
    path               text,
    bytes              bigint,
    sha256             text,
    mime               text,
    duration_s         numeric(10, 3),
    width              integer,
    height             integer,
    tg_file_unique_id  text,
    error              text,
    downloaded_at      timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, tg_message_id)
);
COMMENT ON TABLE core.media IS 'Media fayl (W1). Bitta xabar = bitta media; albom elementlari alohida xabarlar';
COMMENT ON COLUMN core.media.path IS 'paths.media ga nisbatan: yil/oy/guruh/fayl (D-13)';
COMMENT ON COLUMN core.media.message_sent_at IS 'messages.sent_at: xabar partitionini topish uchun';
CREATE INDEX media_status ON core.media (status) WHERE status <> 'downloaded';

-- transcripts (partitioned) -------------------------------------------------------------------------
CREATE TABLE core.transcripts (
    media_id         bigint NOT NULL REFERENCES core.media (id),
    provider         text NOT NULL,
    model            text NOT NULL DEFAULT '',
    message_sent_at  timestamptz NOT NULL,
    source_id        bigint NOT NULL,
    tg_message_id    bigint NOT NULL,
    language         text,
    text             text,
    confidence       real,
    duration_s       numeric(10, 3),
    cost_usd         numeric(12, 6),
    is_primary       boolean NOT NULL DEFAULT true,
    text_purged_at   timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (media_id, provider, model, message_sent_at)
) PARTITION BY RANGE (message_sent_at);
COMMENT ON TABLE core.transcripts IS 'Media matni (W2). Oylik partition xabar vaqti bo''yicha, xabar bilan birga arxivlanadi';
COMMENT ON COLUMN core.transcripts.is_primary IS 'Dashboard ko''rsatadigan asosiy transkript (bake-off da bir nechta provayder)';
CREATE TABLE core.transcripts_default PARTITION OF core.transcripts DEFAULT;
CREATE INDEX transcripts_message ON core.transcripts (source_id, tg_message_id);

-- episodes -------------------------------------------------------------------------------------------
CREATE TABLE core.episodes (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id             bigint NOT NULL REFERENCES core.sources (id),
    started_at            timestamptz NOT NULL,
    ended_at              timestamptz NOT NULL,
    local_day             date GENERATED ALWAYS AS (core.tashkent_day(started_at)) STORED,
    first_tg_message_id   bigint NOT NULL,
    last_tg_message_id    bigint NOT NULL,
    message_count         integer NOT NULL DEFAULT 0,
    participant_tg_ids    bigint[] NOT NULL DEFAULT '{}',
    relevance             text CHECK (relevance IN ('yes', 'no', 'unsure')),
    relevance_confidence  real,
    retain_until          timestamptz,
    text_purged_at        timestamptz,
    segmenter_version     text NOT NULL DEFAULT 'v1',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, first_tg_message_id, segmenter_version),
    CHECK (ended_at >= started_at)
);
COMMENT ON TABLE core.episodes IS 'Suhbat bo''lagi (W2). relevance NULL = hali aniqlanmagan';
COMMENT ON COLUMN core.episodes.relevance IS 'yes | no | unsure (A bosqich, W2-05)';
COMMENT ON COLUMN core.episodes.retain_until IS 'relevance=no bo''lsa: ended_at + config retention.unrelated_text_hours (trigger). Boshqa holatda NULL (saqlanadi)';
CREATE INDEX episodes_source_time ON core.episodes (source_id, started_at);
CREATE INDEX episodes_local_day ON core.episodes (local_day);
CREATE INDEX episodes_purge_due ON core.episodes (retain_until) WHERE relevance = 'no' AND text_purged_at IS NULL;

-- Trigger reads config as the owner, so W2 does not need SELECT on core.config.
CREATE FUNCTION core.episodes_set_retention() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_temp AS $$
DECLARE
    hours numeric;
BEGIN
    IF NEW.relevance = 'no' THEN
        SELECT (value #>> '{}')::numeric INTO hours FROM core.config WHERE key = 'retention.unrelated_text_hours';
        NEW.retain_until := NEW.ended_at + make_interval(hours => coalesce(hours, 72)::int);
    ELSE
        NEW.retain_until := NULL;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER episodes_retention BEFORE INSERT OR UPDATE ON core.episodes
    FOR EACH ROW EXECUTE FUNCTION core.episodes_set_retention();

CREATE TABLE core.episode_messages (
    episode_id     bigint NOT NULL REFERENCES core.episodes (id) ON DELETE CASCADE,
    source_id      bigint NOT NULL,
    tg_message_id  bigint NOT NULL,
    sent_at        timestamptz NOT NULL,
    PRIMARY KEY (episode_id, source_id, tg_message_id)
);
COMMENT ON TABLE core.episode_messages IS 'Episode <-> xabar (W3 [CONTRACT] 6). messages ga FK yo''q (partition), kalit bilan bog''lanadi';
CREATE INDEX episode_messages_message ON core.episode_messages (source_id, tg_message_id);

-- analyses ----------------------------------------------------------------------------------------------
CREATE TABLE core.analyses (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id        bigint NOT NULL REFERENCES core.episodes (id) ON DELETE CASCADE,
    categories        text[] NOT NULL DEFAULT '{}',
    primary_category  text,
    sentiment         text CHECK (sentiment IN ('negative', 'neutral', 'positive', 'mixed')),
    severity          smallint CHECK (severity BETWEEN 0 AND 3),
    summary           text,
    evidence          jsonb NOT NULL DEFAULT '[]',
    entities          jsonb NOT NULL DEFAULT '{}',
    needs_reply       boolean,
    confidence        real,
    model_id          text NOT NULL,
    prompt_version    text NOT NULL,
    is_current        boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (episode_id, model_id, prompt_version)
);
COMMENT ON TABLE core.analyses IS 'Tasnif natijasi (W2). Ko''rsatishda "AI taxmini" + dalil + ishonchlilik (P-08)';
COMMENT ON COLUMN core.analyses.evidence IS '[{"tg_message_id": 123, "quote": "..."}]';
COMMENT ON COLUMN core.analyses.entities IS '{"products": [], "orders": [], "managers": []}';
COMMENT ON COLUMN core.analyses.needs_reply IS 'Javob kerakmi (W3 [CONTRACT] 5)';
COMMENT ON COLUMN core.analyses.is_current IS 'Qayta tahlil bo''lsa eskisi false (W2 boshqaradi); view''lar faqat true ni oladi';
CREATE UNIQUE INDEX analyses_one_current ON core.analyses (episode_id) WHERE is_current;
CREATE INDEX analyses_categories ON core.analyses USING gin (categories);
CREATE INDEX analyses_created_at ON core.analyses (created_at);

-- chat_context, daily stats, daily summaries ---------------------------------------------------------
CREATE TABLE core.chat_context (
    source_id       bigint PRIMARY KEY REFERENCES core.sources (id),
    summary         text,
    open_issues     jsonb NOT NULL DEFAULT '[]',
    window_start    timestamptz,
    window_end      timestamptz,
    model_id        text,
    prompt_version  text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.chat_context IS '24 soatlik yig''ma xulosa va ochiq masalalar (W2-07)';

CREATE TABLE core.daily_stats (
    local_day          date NOT NULL,
    source_id          bigint NOT NULL REFERENCES core.sources (id),
    category           text NOT NULL,
    episode_count      integer NOT NULL DEFAULT 0,
    negative_count     integer NOT NULL DEFAULT 0,
    needs_reply_count  integer NOT NULL DEFAULT 0,
    computed_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (local_day, source_id, category)
);
COMMENT ON TABLE core.daily_stats IS 'Kun yopilgandagi qotirilgan agregat (W2-09). Jonli raqamlar api view''larida';

CREATE TABLE core.daily_summaries (
    local_day       date NOT NULL,
    scope           text NOT NULL CHECK (scope IN ('all', 'category', 'source')),
    scope_key       text NOT NULL DEFAULT '',
    summary         text NOT NULL,
    model_id        text,
    prompt_version  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (local_day, scope, scope_key)
);
COMMENT ON TABLE core.daily_summaries IS 'Kunlik AI xulosa (W2-09; W3 [CONTRACT] 8)';

-- review_queue ---------------------------------------------------------------------------------------
CREATE TABLE core.review_queue (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_type       text NOT NULL CHECK (item_type IN ('analysis', 'relevance', 'transcript', 'response_pair', 'camera_event')),
    item_id         bigint NOT NULL,
    reason          text NOT NULL,
    confidence      real,
    details         jsonb NOT NULL DEFAULT '{}',
    human_decision  text,
    reviewed_by     text,
    reviewed_at     timestamptz,
    status          text GENERATED ALWAYS AS (CASE WHEN reviewed_at IS NULL THEN 'open' ELSE 'resolved' END) STORED,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_type, item_id, reason)
);
COMMENT ON TABLE core.review_queue IS 'Odam tekshiruvi (P-08). W2 qo''shadi; W3 faqat human_decision/reviewed_by/reviewed_at ni yozadi';
CREATE INDEX review_queue_open ON core.review_queue (created_at) WHERE reviewed_at IS NULL;

-- response_pairs (D-19) ------------------------------------------------------------------------------
CREATE TABLE core.response_pairs (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id              bigint NOT NULL REFERENCES core.sources (id),
    request_tg_message_id  bigint NOT NULL,
    requested_at           timestamptz NOT NULL,
    local_day              date GENERATED ALWAYS AS (core.tashkent_day(requested_at)) STORED,
    requester_tg_id        bigint,
    episode_id             bigint REFERENCES core.episodes (id) ON DELETE SET NULL,
    deadline_at            timestamptz NOT NULL,
    status                 text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'missed')),
    answer_tg_message_id   bigint,
    answered_at            timestamptz,
    response_s             numeric GENERATED ALWAYS AS (date_part('epoch', answered_at - requested_at)) STORED,
    responder_tg_id        bigint,
    responder_person_id    bigint REFERENCES core.people (id),
    responder_role         text CHECK (responder_role IN ('employee', 'client', 'unknown')),
    late_answer_at         timestamptz,
    rule_version           text NOT NULL DEFAULT 'v1',
    computed_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, request_tg_message_id, rule_version)
);
COMMENT ON TABLE core.response_pairs IS 'So''rov -> javob (D-19): W2-09 qoida asosida to''ldiradi; W3 v_response_times orqali o''qiydi';
COMMENT ON COLUMN core.response_pairs.status IS 'answered = deadline gacha javob; missed = deadline o''tdi (keyin javob bo''lsa late_answer_at)';
COMMENT ON COLUMN core.response_pairs.response_s IS 'answered_at - requested_at, soniya (generated)';
CREATE INDEX response_pairs_day ON core.response_pairs (local_day, source_id);
CREATE INDEX response_pairs_pending ON core.response_pairs (deadline_at) WHERE status = 'pending';

-- W3 tables -----------------------------------------------------------------------------------------
CREATE TABLE core.report_deliveries (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_type     text NOT NULL CHECK (report_type IN ('tg_daily_excel', 'camera_daily')),
    local_day       date NOT NULL,
    target_chat     text NOT NULL,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts        integer NOT NULL DEFAULT 0,
    file_path       text,
    tg_message_id   bigint,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz,
    UNIQUE (report_type, local_day, target_chat)
);
COMMENT ON TABLE core.report_deliveries IS 'Hisobot yetkazish jurnali (W3-07 yozadi; W3 [CONTRACT] 11)';

CREATE TABLE core.dashboard_users (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login          text NOT NULL UNIQUE,
    password_hash  text NOT NULL,
    role           text NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'manager', 'viewer')),
    camera_access  boolean NOT NULL DEFAULT false,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);
COMMENT ON TABLE core.dashboard_users IS 'Dashboard kirishi va rollar (W3-05; W3 [CONTRACT] 13). Faqat hash saqlanadi (argon2/bcrypt, W3)';

-- retention (P-05 / Q15): executed by W0-08 as w0_admin ------------------------------------------------
CREATE FUNCTION core.purge_unrelated_text(p_now timestamptz DEFAULT now()) RETURNS jsonb
    LANGUAGE plpgsql AS $$
DECLARE
    n_ep bigint; n_msg bigint; n_tr bigint; n_an bigint;
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS _due_episodes (id bigint PRIMARY KEY) ON COMMIT DROP;
    TRUNCATE _due_episodes;
    INSERT INTO _due_episodes
        SELECT id FROM core.episodes
        WHERE relevance = 'no' AND retain_until <= p_now AND text_purged_at IS NULL;

    UPDATE core.messages m SET text = NULL, raw = NULL, text_purged_at = p_now
      FROM core.episode_messages em JOIN _due_episodes d ON d.id = em.episode_id
     WHERE m.source_id = em.source_id AND m.tg_message_id = em.tg_message_id AND m.sent_at = em.sent_at
       AND m.text_purged_at IS NULL;
    GET DIAGNOSTICS n_msg = ROW_COUNT;

    UPDATE core.transcripts t SET text = NULL, text_purged_at = p_now
      FROM core.episode_messages em JOIN _due_episodes d ON d.id = em.episode_id
     WHERE t.source_id = em.source_id AND t.tg_message_id = em.tg_message_id AND t.message_sent_at = em.sent_at
       AND t.text_purged_at IS NULL;
    GET DIAGNOSTICS n_tr = ROW_COUNT;

    UPDATE core.analyses a SET summary = NULL,
           evidence = (SELECT coalesce(jsonb_agg(e - 'quote'), '[]') FROM jsonb_array_elements(a.evidence) e)
      FROM _due_episodes d WHERE a.episode_id = d.id;
    GET DIAGNOSTICS n_an = ROW_COUNT;

    UPDATE core.episodes e SET text_purged_at = p_now FROM _due_episodes d WHERE e.id = d.id;
    GET DIAGNOSTICS n_ep = ROW_COUNT;

    RETURN jsonb_build_object('episodes', n_ep, 'messages', n_msg, 'transcripts', n_tr, 'analyses', n_an);
END $$;
COMMENT ON FUNCTION core.purge_unrelated_text(timestamptz) IS 'P-05: muddati o''tgan relevance=no episode matnini o''chiradi, metadata qoladi (W0-08 chaqiradi)';
