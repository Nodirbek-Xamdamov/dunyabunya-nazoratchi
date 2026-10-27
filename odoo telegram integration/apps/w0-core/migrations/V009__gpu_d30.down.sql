DROP FUNCTION core.gpu_device_for(text);
ALTER TABLE core.gpu_overrides DROP CONSTRAINT gpu_overrides_mode_check;
ALTER TABLE core.gpu_overrides ADD CONSTRAINT gpu_overrides_mode_check CHECK (mode IN ('coresident', 'timeslice'));
DELETE FROM core.config WHERE key IN ('gpu.vision_idle_to_llm', 'gpu.vision_idle_minutes', 'gpu.devices');
UPDATE core.config SET value = '"coresident"',
       description = 'D-14: coresident (Gemma + YOLO birga, 1 kamera bosqichi) | timeslice (zaxira). Qayta ishga tushirishsiz o''zgaradi'
 WHERE key = 'gpu.mode';
UPDATE core.config SET value = '"08:00-00:00"', description = 'timeslice: Telegram (llm) oynasi, Asia/Tashkent (D-04)'
 WHERE key = 'gpu.llm_window';
UPDATE core.config SET value = '"00:00-08:00"', description = 'timeslice: kamera (vision) oynasi'
 WHERE key = 'gpu.vision_window';
