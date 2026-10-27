"""W0-01 smoke test: Gemma 4 12B on Ollama - 100-token generation speed and image input."""
import base64
import json
import sys
import urllib.request

URL = "http://127.0.0.1:11434/api/generate"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemma4:12b"
IMAGE = sys.argv[2] if len(sys.argv) > 2 else None


def call(payload):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def report(tag, r):
    gen_tps = r["eval_count"] / (r["eval_duration"] / 1e9)
    pp_tps = r.get("prompt_eval_count", 0) / max(r.get("prompt_eval_duration", 1) / 1e9, 1e-9)
    print(f"[{tag}] load={r.get('load_duration', 0)/1e9:.1f}s prompt_tokens={r.get('prompt_eval_count')} "
          f"prompt_tps={pp_tps:.0f} gen_tokens={r['eval_count']} gen_tps={gen_tps:.1f} total={r['total_duration']/1e9:.1f}s")
    print(f"[{tag}] text: {r['response'][:300]!r}")


opts = {"num_predict": 100, "temperature": 0, "num_ctx": 8192}
# Cold start (includes model load), then warm run for steady-state speed
for tag in ("cold", "warm"):
    r = call({"model": MODEL, "prompt": "Write a short story about a construction materials shop in Tashkent.",
              "stream": False, "think": False, "options": {**opts, "num_predict": 100}})
    report(tag, r)

if IMAGE:
    b64 = base64.b64encode(open(IMAGE, "rb").read()).decode()
    r = call({"model": MODEL, "prompt": "Describe this image: what text is written and which shapes/colors are present?",
              "images": [b64], "stream": False, "think": False, "options": {**opts, "num_predict": 120}})
    report("image", r)
