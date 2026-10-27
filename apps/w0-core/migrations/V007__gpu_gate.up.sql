-- V007 GPU gate (W0-06, CONTRACTS §3, D-14, D-24). The gate process (w0_admin) is the only one that loads
-- and unloads models. It publishes which gpu_class may be claimed now; workers ask
-- core.gpu_allowed_classes() (genesis_queue does it in claim). Stale publication (gate dead) = only 'none'.

INSERT INTO core.config (key, value, description) VALUES
  ('gpu.mode', '"coresident"', 'D-14: coresident (Gemma + YOLO birga, 1 kamera bosqichi) | timeslice (zaxira). Qayta ishga tushirishsiz o''zgaradi'),
  ('gpu.llm_window', '"08:00-00:00"', 'timeslice: Telegram (llm) oynasi, Asia/Tashkent (D-04)'),
  ('gpu.vision_window', '"00:00-08:00"', 'timeslice: kamera (vision) oynasi'),
  ('gpu.switch_guard_minutes', '15', 'Almashishdan shuncha daqiqa oldin chiqib ketadigan sinfga yangi job berilmaydi (CONTRACTS §3)'),
  ('gpu.drain_timeout_minutes', '30', 'Almashishda ishlayotgan joblar shuncha kutiladi; keyin model baribir almashadi (job lease bilan qaytadi)'),
  ('gpu.vram_limit_mib', '13500', 'Jami VRAM chegarasi: W0-01 o''lchovi coresident eng ko''pi 10605 MiB + ~2.9 GB zaxira (16311 dan)'),
  ('gpu.llm_model', '"gemma4:12b"', 'Ollama modeli (D-03, D-14)'),
  ('gpu.llm_model_mib', '9500', 'Model yuklashdan oldin bo''sh bo''lishi kerak VRAM (W0-01: ~9.2 GB ctx 8192)'),
  ('gpu.publish_seconds', '90', 'E''lon qilingan ruxsat shuncha soniya amal qiladi; gate o''lsa ishchilar faqat none oladi');

CREATE TABLE core.gpu_gate_state (
    id               smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    mode             text NOT NULL,
    phase            text NOT NULL,
    allowed_classes  text[] NOT NULL,
    valid_until      timestamptz NOT NULL,
    loaded_models    jsonb NOT NULL DEFAULT '[]',
    vram_used_mib    integer,
    vram_total_mib   integer,
    note             text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.gpu_gate_state IS 'Gate e''loni (bitta qator): hozir qaysi gpu_class olinadi, qaysi model yuklangan, VRAM';
COMMENT ON COLUMN core.gpu_gate_state.phase IS 'steady | guard (almashish oldidan) | draining (ishlayotganlar kutilmoqda) | loading | over_vram_limit | override';
INSERT INTO core.gpu_gate_state (mode, phase, allowed_classes, valid_until, note)
VALUES ('coresident', 'init', '{none}', now(), 'gate hali ishga tushmagan');

CREATE TABLE core.gpu_overrides (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode             text CHECK (mode IN ('coresident', 'timeslice')),
    allowed_classes  text[],
    starts_at        timestamptz NOT NULL DEFAULT now(),
    ends_at          timestamptz NOT NULL,
    reason           text NOT NULL,
    created_by       text NOT NULL DEFAULT current_user,
    created_at       timestamptz NOT NULL DEFAULT now(),
    cancelled_at     timestamptz,
    cancelled_by     text,
    CHECK (mode IS NOT NULL OR allowed_classes IS NOT NULL),
    CHECK (ends_at > starts_at)
);
COMMENT ON TABLE core.gpu_overrides IS 'Qo''lda override jurnali (genesis_gpu override). Faol = starts_at <= hozir < ends_at va bekor qilinmagan; eng yangisi amal qiladi';

CREATE TABLE core.gpu_gate_log (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at       timestamptz NOT NULL DEFAULT now(),
    sim_at   timestamptz,
    event    text NOT NULL,
    details  jsonb NOT NULL DEFAULT '{}'
);
COMMENT ON TABLE core.gpu_gate_log IS 'Gate jurnali: rejim/oyna almashishi, model yuklash/bo''shatish (VRAM oldin/keyin), override, begona GPU jarayonlari';
COMMENT ON COLUMN core.gpu_gate_log.sim_at IS 'Tezlatilgan soat bilan sinovda simulyatsiya vaqti (oddiy ishda NULL)';
CREATE INDEX gpu_gate_log_at ON core.gpu_gate_log (at);

CREATE FUNCTION core.gpu_allowed_classes() RETURNS text[]
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
    SELECT coalesce((SELECT CASE WHEN valid_until > now() THEN allowed_classes ELSE ARRAY['none'] END
                       FROM core.gpu_gate_state WHERE id = 1), ARRAY['none'])
$$;
COMMENT ON FUNCTION core.gpu_allowed_classes() IS 'Hozir olinishi mumkin bo''lgan gpu_class lar (gate e''loni; eskirgan bo''lsa faqat none)';

CREATE VIEW api.v_gpu_gate AS
SELECT s.mode, s.phase, s.allowed_classes, s.valid_until, (s.valid_until > now()) AS fresh, s.loaded_models,
       s.vram_used_mib, s.vram_total_mib, s.note, s.updated_at,
       (SELECT jsonb_build_object('id', o.id, 'mode', o.mode, 'allowed_classes', o.allowed_classes, 'ends_at', o.ends_at,
                                  'reason', o.reason, 'created_by', o.created_by)
          FROM core.gpu_overrides o
         WHERE o.cancelled_at IS NULL AND o.starts_at <= now() AND o.ends_at > now()
         ORDER BY o.created_at DESC LIMIT 1) AS active_override
FROM core.gpu_gate_state s;
