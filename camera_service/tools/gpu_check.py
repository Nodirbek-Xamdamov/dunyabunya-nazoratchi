"""Stage 1: prove that PyTorch really uses this GPU (RTX 5060 Ti, sm_120).

    .venv\\Scripts\\python.exe tools\\gpu_check.py [--yolo models\\yolo26m.pt]

Prints torch/CUDA versions, the device and its compute capability, times a matrix
multiplication on the GPU and checks it against the CPU, and with --yolo runs the
model once on the sample picture that ships with Ultralytics.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camsvc import runtime  # noqa: E402  (must come before ultralytics)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yolo")
    args = parser.parse_args()

    import torch

    print(f"torch {torch.__version__}, CUDA {torch.version.cuda}, cuDNN {torch.backends.cudnn.version()}")
    print(f"cuda.is_available: {torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        return 1
    cap = torch.cuda.get_device_capability(0)
    print(f"device: {torch.cuda.get_device_name(0)}, capability {cap}, arch list {torch.cuda.get_arch_list()}")
    ok = cap == (12, 0)
    print(f"capability == (12, 0): {ok}")

    a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
    b = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(20):
        c = a @ b
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    tflops = 20 * 2 * 4096**3 / elapsed / 1e12
    small_a, small_b = a[:256, :256].float(), b[:256, :256].float()
    diff = (small_a @ small_b - (small_a.cpu() @ small_b.cpu()).cuda()).abs().max().item()
    print(f"matmul fp16 4096x4096 x20: {elapsed * 1000:.0f} ms ({tflops:.1f} TFLOPS), GPU vs CPU max diff {diff:.2e}")
    del a, b, c

    if args.yolo:
        runtime.check()
        from ultralytics import YOLO
        from ultralytics.utils import ASSETS

        model = YOLO(args.yolo)
        image = str(ASSETS / "bus.jpg")
        times = []
        for i in range(25):  # the first calls include warm-up
            t0 = time.perf_counter()
            result = model.predict(image, classes=[0], half=True, device=0, verbose=False)[0]
            torch.cuda.synchronize()
            if i >= 5:
                times.append((time.perf_counter() - t0) * 1000)
        confs = [round(c, 2) for c in result.boxes.conf.tolist()]
        print(f"YOLO {Path(args.yolo).name} on bus.jpg: {len(confs)} people {confs}, median"
              f" {sorted(times)[len(times) // 2]:.1f} ms, device {model.predictor.device},"
              f" fp16 {model.predictor.model.fp16}, boxes on {result.boxes.data.device}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
