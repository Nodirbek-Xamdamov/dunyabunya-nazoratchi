"""W0-01 smoke test: GPU matmul throughput with PyTorch (fp16 and fp32)."""
import time

import torch


def bench(dtype, n=8192, iters=20):
    a = torch.randn(n, n, device="cuda", dtype=dtype)
    b = torch.randn(n, n, device="cuda", dtype=dtype)
    for _ in range(3):
        a @ b
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        c = a @ b
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    tflops = 2 * n**3 * iters / dt / 1e12
    # Correctness check against fp32 on a small slice
    ref = (a[:64].float() @ b[:, :64].float())
    err = (c[:64, :64].float() - ref).abs().max().item() / ref.abs().max().item()
    return tflops, err


print("torch", torch.__version__, "cuda", torch.version.cuda)
print("device", torch.cuda.get_device_name(0), "cc", torch.cuda.get_device_capability(0))
for dt in (torch.float16, torch.float32):
    tf, err = bench(dt)
    print(f"{dt}: {tf:.1f} TFLOPS, rel_err={err:.2e}")
print("max_mem_allocated_MiB", round(torch.cuda.max_memory_allocated() / 2**20))
