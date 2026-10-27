"""W0-01 smoke test: Gemma 4 12B on llama.cpp server - 100-token speed, image input, JSON schema output."""
import base64
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
IMAGE = sys.argv[2] if len(sys.argv) > 2 else None


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def chat(tag, content, max_tokens=100, **extra):
    r = post("/v1/chat/completions", {"messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
                                      "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}, **extra})
    t = r.get("timings", {})
    print(f"[{tag}] prompt_tokens={t.get('prompt_n')} prompt_tps={t.get('prompt_per_second', 0):.0f} "
          f"gen_tokens={t.get('predicted_n')} gen_tps={t.get('predicted_per_second', 0):.1f}")
    print(f"[{tag}] text: {r['choices'][0]['message']['content'][:300]!r}")
    return r


prompt = "Write a short story about a construction materials shop in Tashkent."
for tag in ("run1", "run2"):
    chat(tag, prompt)

if IMAGE:
    b64 = base64.b64encode(open(IMAGE, "rb").read()).decode()
    chat("image", [{"type": "text", "text": "Describe this image: what text is written and which shapes/colors are present?"},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}], max_tokens=120)

schema = {"type": "object", "properties": {"category": {"type": "string", "enum": ["order", "complaint", "other"]},
                                           "confidence": {"type": "number"}}, "required": ["category", "confidence"]}
r = chat("json", "Classify: 'Mijoz: sement 20 qop kerak, ertaga yetkazasizmi?'", max_tokens=60,
         response_format={"type": "json_schema", "json_schema": {"name": "c", "schema": schema}})
print("[json] parsed:", json.loads(r["choices"][0]["message"]["content"]))
