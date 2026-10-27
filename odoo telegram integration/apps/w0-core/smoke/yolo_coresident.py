"""W0-01 smoke test: YOLO on GPU alone and together with Gemma (Ollama) - speed and peak VRAM."""
import json
import subprocess
import sys
import threading
import time
import urllib.request

from ultralytics import YOLO

WEIGHTS = sys.argv[1]
IMAGE = sys.argv[2]
OLLAMA_MODEL = "gemma4:12b"


def vram_used():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


class VramSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, False

    def run(self):
        while not self.stop:
            self.peak = max(self.peak, vram_used())
            time.sleep(0.3)


def yolo_bench(model, n):
    t0 = time.perf_counter()
    for _ in range(n):
        res = model.predict(IMAGE, device=0, imgsz=640, verbose=False, save=False)
    dt = (time.perf_counter() - t0) / n * 1000
    return dt, res[0]


def gemma_gen(result, num_predict=400):
    body = {"model": OLLAMA_MODEL, "prompt": "Write a detailed essay about cement logistics.", "stream": False,
            "think": False, "options": {"num_predict": num_predict, "temperature": 0, "num_ctx": 8192}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    result["tps"] = d["eval_count"] / (d["eval_duration"] / 1e9)
    result["tokens"] = d["eval_count"]


base = vram_used()
print(f"VRAM before YOLO (Gemma state as-is): {base} MiB")
model = YOLO(WEIGHTS)
model.predict(IMAGE, device=0, imgsz=640, verbose=False, save=False)  # warmup / CUDA init
ms, r0 = yolo_bench(model, 50)
names = [model.names[int(c)] for c in r0.boxes.cls.tolist()]
print(f"[yolo alone] {WEIGHTS.split(chr(92))[-1]} device={r0.boxes.data.device} {ms:.1f} ms/img "
      f"detections={len(names)} {sorted(set(names))}")
print(f"VRAM with YOLO loaded: {vram_used()} MiB")

# Coresident: Gemma generation and YOLO loop at the same time
sampler = VramSampler()
sampler.start()
g = {}
th = threading.Thread(target=gemma_gen, args=(g,))
th.start()
ms_co, _ = yolo_bench(model, 100)
th.join()
sampler.stop = True
sampler.join()
print(f"[coresident] yolo {ms_co:.1f} ms/img, gemma {g['tps']:.1f} tok/s ({g['tokens']} tok), "
      f"peak VRAM {sampler.peak} MiB of 16311")
