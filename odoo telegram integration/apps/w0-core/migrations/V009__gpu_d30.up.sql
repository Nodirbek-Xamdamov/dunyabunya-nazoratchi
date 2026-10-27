-- V009 D-30 (plan v1.3): one RTX 5060 Ti 16 GB. Standard mode timeslice 12/12 (Telegram/Gemma 10:00-22:00, camera/YOLO
-- 22:00-10:00, windows in settings); an idle camera window is lent back to Gemma and taken again when a camera job
-- arrives; coresident (D-14) stays as the fallback; dedicated (second GPU) = settings + logic, full test when it arrives.
-- V007 is applied and not edited: values change here.

UPDATE core.config SET value = '"timeslice"',
       description = 'D-30: timeslice (standart, 12/12) | coresident (zaxira, D-14) | dedicated (ikkinchi GPU kelganda). Qayta ishga tushirishsiz o''zgaradi'
 WHERE key = 'gpu.mode';
UPDATE core.config SET value = '"10:00-22:00"',
       description = 'D-30: timeslice Telegram (Gemma, llm) oynasi, Asia/Tashkent'
 WHERE key = 'gpu.llm_window';
UPDATE core.config SET value = '"22:00-10:00"',
       description = 'D-30: timeslice kamera (YOLO, vision) oynasi, Asia/Tashkent; kamera tahlili bir kun orqada qolishi mumkin'
 WHERE key = 'gpu.vision_window';

INSERT INTO core.config (key, value, description) VALUES
  ('gpu.vision_idle_to_llm', 'true', 'D-30: kamera oynasida vision job bo''lmasa GPU Gemma ga qaytariladi, job kelsa yana kameraga (oyna tugashiga guard daqiqadan kam qolsa kameraga o''tilmaydi)'),
  ('gpu.vision_idle_minutes', '5', 'Kamera ishi tugagach shuncha daqiqa yangi vision job bo''lmasa GPU Gemma ga qaytadi (tez-tez almashmaslik uchun)'),
  ('gpu.devices', '{"llm": 0, "vision": 0}', 'D-30 dedicated: gpu_class -> nvidia-smi qurilma raqami. Hozir bitta GPU (ikkalasi 0); ikkinchi GPU kelganda masalan {"llm": 0, "vision": 1}');

ALTER TABLE core.gpu_overrides DROP CONSTRAINT gpu_overrides_mode_check;
ALTER TABLE core.gpu_overrides ADD CONSTRAINT gpu_overrides_mode_check CHECK (mode IN ('coresident', 'timeslice', 'dedicated'));

CREATE FUNCTION core.gpu_device_for(gpu_class text) RETURNS integer
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
    SELECT coalesce((SELECT (value ->> gpu_class)::integer FROM core.config WHERE key = 'gpu.devices'), 0)
$$;
COMMENT ON FUNCTION core.gpu_device_for(text) IS 'D-30: shu gpu_class ishlatadigan GPU raqami (gpu.devices; yo''q bo''lsa 0). W2/W4 ishchilari modelni shu qurilmaga yuklaydi';
