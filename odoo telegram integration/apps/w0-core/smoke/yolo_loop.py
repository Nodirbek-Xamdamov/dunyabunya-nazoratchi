"""W0-06 coresident test helper: run YOLO on the GPU for N seconds, print one JSON line with frames and ms/frame.
Run with the camera_service venv (torch + ultralytics):  python -I yolo_loop.py <weights> <image> <seconds>"""
import json
import sys
import time

import torch
from ultralytics import YOLO

weights, image, seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
model = YOLO(weights)
model.predict(image, device=0, imgsz=640, verbose=False, save=False)  # warm-up / load to GPU
frames, t0 = 0, time.perf_counter()
detections = 0
while time.perf_counter() - t0 < seconds:
    res = model.predict(image, device=0, imgsz=640, verbose=False, save=False)
    detections = len(res[0].boxes)
    frames += 1
elapsed = time.perf_counter() - t0
print(json.dumps({"frames": frames, "ms_per_frame": round(elapsed / frames * 1000, 1), "detections": detections,
                  "device": str(model.predictor.device),
                  "cuda_mem_mib": round(torch.cuda.max_memory_reserved() / 2**20)}), flush=True)
