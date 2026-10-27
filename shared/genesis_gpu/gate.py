"""The GPU gate (W0-06). Runs as w0_admin; the only component that loads/unloads models (CONTRACTS §3).

Each tick: read gpu.* from core.config and the active override (so mode changes need no restart), decide
the window (D-30 timeslice 12/12 is the standard; coresident is the fallback; dedicated needs a second GPU),
drain running jobs of the outgoing class, unload/load the Ollama model, check VRAM against gpu.vram_limit_mib,
publish the allowed gpu classes to core.gpu_gate_state (valid for gpu.publish_seconds; workers fall back to
'none' when it goes stale) and journal every change to core.gpu_gate_log.
D-30: in the camera window with no camera job the GPU goes back to Gemma; a camera job takes it again.
YOLO is loaded by the W4 worker inside a `vision` job; the gate controls it by allowing/withholding `vision`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

import psycopg
from psycopg.types.json import Jsonb

from .devices import Ollama, gpu_count, gpu_memory, gpu_processes
from .policy import decide

TASHKENT = timezone(timedelta(hours=5))  # no DST in Uzbekistan
OURS = ("ollama", "llama-server")  # llama-server.exe = the Ollama model runner


class GpuGate:
    def __init__(self, conn: psycopg.Connection, ollama: Ollama | None = None,
                 clock: Callable[[], datetime] | None = None, simulated: bool = False):
        self.conn = conn
        self.ollama = ollama or Ollama()
        self.clock = clock or (lambda: datetime.now(TASHKENT))
        self.simulated = simulated
        self._drain_since: dict[str, datetime] = {}
        self._last_published: tuple | None = None
        self._last_foreign: tuple | None = None
        self._last_window: str | None = None
        self._vision_seen: datetime | None = None
        self._dedicated_problem: str | None = None
        self._over_limit = False

    # -- database helpers ----------------------------------------------------------------------------
    def _q(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def config(self) -> dict:
        return {k: v for k, v in self._q("SELECT key, value FROM core.config WHERE key LIKE 'gpu.%'")}

    def override(self) -> dict | None:
        rows = self._q("""SELECT id, mode, allowed_classes, ends_at, reason FROM core.gpu_overrides
                          WHERE cancelled_at IS NULL AND starts_at <= now() AND ends_at > now()
                          ORDER BY created_at DESC LIMIT 1""")
        return dict(zip(("id", "mode", "allowed_classes", "ends_at", "reason"), rows[0])) if rows else None

    def running(self) -> dict[str, int]:
        return {c: n for c, n in self._q("""SELECT gpu_class, count(*) FROM core.jobs
                                            WHERE status = 'running' AND lease_until > now() GROUP BY gpu_class""")}

    def vision_demand(self) -> int:
        """Camera jobs waiting (ready now) or running, incl. ones whose lease ran out (they come back)."""
        return self._q("""SELECT count(*) FROM core.jobs WHERE gpu_class = 'vision'
                          AND (status = 'running' OR (status IN ('queued', 'failed') AND run_after <= now()))""")[0][0]

    def log(self, event: str, **details) -> None:
        self._q("INSERT INTO core.gpu_gate_log (sim_at, event, details) VALUES (%s, %s, %s)",
                (self.clock() if self.simulated else None, event, Jsonb(details)))

    # -- one step ------------------------------------------------------------------------------------
    def _drain_expired(self, cls: str, now: datetime, timeout_min: float) -> bool:
        since = self._drain_since.setdefault(cls, now)
        return now - since >= timedelta(minutes=timeout_min)

    def _dedicated_ok(self, devices: dict) -> str | None:
        """None if dedicated can run; otherwise why not (D-30: needs a second GPU)."""
        llm_dev, vis_dev = int(devices.get("llm", 0)), int(devices.get("vision", 0))
        if llm_dev == vis_dev:
            return f"gpu.devices: llm va vision bir xil qurilmada ({llm_dev}), ikkinchi GPU kerak"
        count = gpu_count()
        if max(llm_dev, vis_dev) >= count:
            return f"gpu.devices {devices}: serverda {count} ta GPU"
        return None

    def tick(self) -> dict:
        cfg = self.config()
        ov = self.override()
        now = self.clock()
        requested = (ov or {}).get("mode") or cfg["gpu.mode"]
        mode = requested
        model = cfg["gpu.llm_model"]
        devices = cfg.get("gpu.devices") or {"llm": 0, "vision": 0}
        notes: list[str] = []

        if requested == "dedicated":
            problem = self._dedicated_ok(devices)
            if problem:
                mode = "timeslice"  # the standard (D-30) until the second GPU is there
                notes.append(f"dedicated mavjud emas -> timeslice: {problem}")
            if problem != self._dedicated_problem:
                self.log("dedicated_unavailable" if problem else "dedicated_ready", reason=problem, devices=devices)
                self._dedicated_problem = problem
        llm_device = int(devices.get("llm", 0)) if mode == "dedicated" else None

        demand = self.vision_demand()
        if demand:
            self._vision_seen = now
        idle_min = float(cfg.get("gpu.vision_idle_minutes", 5))
        vision_active = self._last_window == "vision"
        vision_wanted = bool(demand) or (vision_active and self._vision_seen is not None
                                         and now - self._vision_seen < timedelta(minutes=idle_min))
        d = decide(now, mode, cfg["gpu.llm_window"], cfg["gpu.vision_window"], int(cfg["gpu.switch_guard_minutes"]),
                   vision_wanted=vision_wanted, vision_active=vision_active,
                   idle_to_llm=bool(cfg.get("gpu.vision_idle_to_llm", False)))
        if d.window != self._last_window and {d.window, self._last_window} == {"vision", "llm_idle"}:
            self.log("camera_window_" + ("taken" if d.window == "vision" else "lent_to_llm"), vision_jobs=demand)
        if d.window == "llm_idle":
            notes.append("kamera oynasi bo'sh: GPU Gemma da (D-30)")
        target = set(d.target)
        phase = "guard" if d.guard else "steady"
        running = self.running()
        drain_timeout = float(cfg["gpu.drain_timeout_minutes"])

        foreign = tuple(sorted((p["pid"], p["name"]) for p in gpu_processes() if not p["name"].lower().startswith(OURS)))
        if foreign != self._last_foreign:
            self.log("gpu_processes", processes=[{"pid": p, "name": n} for p, n in foreign],
                     note="boshqa GPU foydalanuvchilari: faqat qayd, tegilmaydi")
            self._last_foreign = foreign

        llm_loaded = model in self.ollama.loaded()

        # outgoing LLM: wait for running llm jobs, then unload and prove VRAM was freed
        if not d.want_llm and llm_loaded:
            target.discard("llm")
            if running.get("llm") and not self._drain_expired("llm", now, drain_timeout):
                phase = "draining"
                notes.append(f"{running['llm']} llm job tugashi kutilmoqda")
            else:
                if running.get("llm"):
                    self.log("drain_timeout", gpu_class="llm", running=running["llm"])
                before = gpu_memory().used_mib
                ok = self.ollama.unload(model)
                after = gpu_memory().used_mib
                self.log("model_unloaded", model=model, ok=ok, vram_before_mib=before, vram_after_mib=after,
                         freed_mib=before - after, window=d.window)
                llm_loaded = not ok
                self._drain_since.pop("llm", None)
        else:
            self._drain_since.pop("llm", None)

        # incoming LLM: in timeslice the vision jobs must finish first; VRAM must have room
        if d.want_llm and not llm_loaded:
            if mode == "timeslice" and running.get("vision") and not self._drain_expired("vision", now, drain_timeout):
                phase = "draining"
                target.discard("vision")
                notes.append(f"{running['vision']} vision job tugashi kutilmoqda")
            elif gpu_memory(llm_device).free_mib < int(cfg["gpu.llm_model_mib"]):
                phase = "waiting_vram"
                notes.append("modelga VRAM yetmaydi")
            else:
                before = gpu_memory(llm_device).used_mib
                secs = self.ollama.load(model, main_gpu=llm_device)
                after = gpu_memory(llm_device).used_mib
                self.log("model_loaded", model=model, seconds=round(secs, 1), vram_before_mib=before,
                         vram_after_mib=after, window=d.window, mode=mode, device=llm_device)
                llm_loaded = True
                self._drain_since.pop("vision", None)
        if not llm_loaded:
            target.discard("llm")

        # timeslice camera window: vision only after the LLM is gone
        if mode == "timeslice" and "vision" in target and (llm_loaded or running.get("llm")):
            target.discard("vision")
            phase = "draining"

        limit = int(cfg["gpu.vram_limit_mib"])
        watched = sorted({int(devices.get("llm", 0)), int(devices.get("vision", 0))}) if mode == "dedicated" else [None]
        mems = {dev: gpu_memory(dev) for dev in watched}
        mem = mems[watched[0]]
        over = {dev: m.used_mib for dev, m in mems.items() if m.used_mib > limit}
        if over:
            target = {"none"}
            phase = "over_vram_limit"
            notes.append(f"VRAM {max(over.values())} > {limit} MiB")
            if not self._over_limit:
                self.log("vram_over_limit", used_mib=max(over.values()), limit_mib=limit,
                         devices={str(k): v for k, v in over.items()})
            self._over_limit = True
        else:
            self._over_limit = False

        if ov and ov.get("allowed_classes") is not None:
            target = set(ov["allowed_classes"]) | {"none"}
            phase = "override"
            notes.append(f"override #{ov['id']}: {ov['reason']}")

        allowed = sorted(target | {"none"})
        loaded = self.ollama.ps()
        self._q("""UPDATE core.gpu_gate_state SET mode = %s, phase = %s, allowed_classes = %s,
                       valid_until = now() + make_interval(secs => %s), loaded_models = %s, vram_used_mib = %s,
                       vram_total_mib = %s, note = %s, updated_at = now() WHERE id = 1""",
                (mode, phase, allowed, float(cfg["gpu.publish_seconds"]), Jsonb(loaded), mem.used_mib, mem.total_mib,
                 "; ".join(notes) or None))
        self._q("""INSERT INTO core.heartbeats (component, instance, last_beat_at, started_at, status, version, details)
                   VALUES ('w0_gpu_gate', 'main', now(), now(), 'ok', 'v2', %s)
                   ON CONFLICT (component, instance) DO UPDATE SET last_beat_at = now(), status = 'ok', version = 'v2',
                       details = EXCLUDED.details""",
                (Jsonb({"mode": mode, "requested_mode": requested, "window": d.window, "phase": phase, "allowed": allowed,
                        "vision_jobs": demand}),))
        state = (mode, d.window, phase, tuple(allowed), tuple(m["name"] for m in loaded))
        if state != self._last_published:
            self.log("state", mode=mode, window=d.window, phase=phase, allowed=allowed,
                     loaded=[m["name"] for m in loaded], vram_used_mib=mem.used_mib, note="; ".join(notes) or None)
            self._last_published = state
        self._last_window = d.window
        return {"at": now.isoformat(timespec="minutes"), "mode": mode, "requested_mode": requested, "window": d.window,
                "phase": phase, "allowed": allowed, "loaded": [m["name"] for m in loaded], "vram_used_mib": mem.used_mib,
                "running": running, "vision_jobs": demand, "note": "; ".join(notes)}


    def publish_failure(self, error: str) -> None:
        """A tick failed (Ollama or nvidia-smi down...): withdraw every GPU class at once instead of letting the last
        publication (e.g. 'llm' allowed) stay valid for gpu.publish_seconds."""
        self._q("""UPDATE core.gpu_gate_state SET phase = 'error', allowed_classes = '{none}', note = %s,
                       valid_until = now() + make_interval(secs => (SELECT (value #>> '{}')::numeric FROM core.config
                                                                  WHERE key = 'gpu.publish_seconds')),
                       updated_at = now() WHERE id = 1""", (error[:500],))
        self._q("""UPDATE core.heartbeats SET last_beat_at = now(), status = 'error' WHERE component = 'w0_gpu_gate'""")
        if self._last_published != ("error",):
            self.log("tick_failed", error=error[:500])
            self._last_published = ("error",)


def device_for(conn: psycopg.Connection, gpu_class: str) -> int:
    """D-30: the GPU (nvidia-smi index) a worker uses for this class (core.gpu_device_for; 0 on one GPU)."""
    with conn.cursor() as cur:
        cur.execute("SELECT core.gpu_device_for(%s)", (gpu_class,))
        return cur.fetchone()[0]


def describe(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False)
