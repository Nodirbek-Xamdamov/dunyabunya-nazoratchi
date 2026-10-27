DROP VIEW api.v_gpu_gate;
DROP FUNCTION core.gpu_allowed_classes();
DROP TABLE core.gpu_gate_log;
DROP TABLE core.gpu_overrides;
DROP TABLE core.gpu_gate_state;
DELETE FROM core.config WHERE key IN ('gpu.mode', 'gpu.llm_window', 'gpu.vision_window', 'gpu.switch_guard_minutes',
    'gpu.drain_timeout_minutes', 'gpu.vram_limit_mib', 'gpu.llm_model', 'gpu.llm_model_mib', 'gpu.publish_seconds');
