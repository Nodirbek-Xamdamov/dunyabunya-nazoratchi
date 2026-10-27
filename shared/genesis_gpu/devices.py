"""Ollama HTTP API and nvidia-smi readers (no third-party dependencies)."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass


class Ollama:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout_s: float = 300):
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s

    def _call(self, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def ps(self) -> list[dict]:
        """Loaded models: name, size, size_vram, expires_at."""
        return [{"name": m["name"], "size_vram_mib": round(m.get("size_vram", 0) / 2**20), "expires_at": m.get("expires_at")}
                for m in self._call("/api/ps").get("models", [])]

    def loaded(self) -> set[str]:
        return {m["name"] for m in self.ps()}

    def load(self, model: str, keep_alive: int | str = -1, main_gpu: int | None = None) -> float:
        """Load into VRAM and keep it there (keep_alive -1). `main_gpu` (dedicated, D-30) pins it to one GPU.
        Returns seconds taken."""
        t0 = time.monotonic()
        body = {"model": model, "prompt": "", "keep_alive": keep_alive, "stream": False}
        if main_gpu is not None:
            body["options"] = {"main_gpu": main_gpu}
        self._call("/api/generate", body)
        return time.monotonic() - t0

    def unload(self, model: str, wait_s: float = 30) -> bool:
        """Ask Ollama to free the model (keep_alive 0) and wait until it is gone."""
        self._call("/api/generate", {"model": model, "prompt": "", "keep_alive": 0, "stream": False})
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if model not in self.loaded():
                return True
            time.sleep(0.5)
        return False

    def generate(self, model: str, prompt: str, num_predict: int = 64) -> dict:
        """Used by tests to prove the model works; keeps it loaded (keep_alive -1)."""
        return self._call("/api/generate", {"model": model, "prompt": prompt, "stream": False, "keep_alive": -1,
                                            "options": {"num_predict": num_predict}})


@dataclass(frozen=True)
class GpuMemory:
    used_mib: int
    total_mib: int
    util_pct: int

    @property
    def free_mib(self) -> int:
        return self.total_mib - self.used_mib


def _smi(args: list[str]) -> list[list[str]]:
    out = subprocess.run(["nvidia-smi", *args, "--format=csv,noheader,nounits"], capture_output=True,
                         encoding="mbcs", errors="replace", check=True).stdout
    return [[c.strip() for c in line.split(",")] for line in out.splitlines() if line.strip()]


def gpu_memory(index: int | None = None) -> GpuMemory:
    """Memory of one GPU (nvidia-smi index; None = the first one)."""
    args = ["--query-gpu=memory.used,memory.total,utilization.gpu"] + ([] if index is None else ["-i", str(index)])
    used, total, util = _smi(args)[0]
    return GpuMemory(int(used), int(total), int(util))


def gpu_count() -> int:
    return len(_smi(["--query-gpu=index"]))


def gpu_processes() -> list[dict]:
    """Processes holding the GPU. On Windows (WDDM) per-process memory is not reported."""
    rows = _smi(["--query-compute-apps=pid,process_name,used_memory"])
    return [{"pid": int(r[0]), "name": r[1].replace("\\", "/").rsplit("/", 1)[-1], "path": r[1],
             "used_mib": None if not r[2].isdigit() else int(r[2])} for r in rows if r and r[0].isdigit()]
