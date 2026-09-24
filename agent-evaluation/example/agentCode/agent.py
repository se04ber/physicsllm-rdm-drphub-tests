#!/usr/bin/env python3
"""The smallest agent the wrapper can measure.

Reads one case as JSON on stdin, prints {"output": "..."} on stdout.
Replace the body with your own agent and keep that contract.

Model calls go to OPENAI_BASE_URL, which the wrapper points at its own
token-counting proxy. Nothing here needs to know where the model really is.
"""
import json
import os
import sys
import urllib.request

case = json.load(sys.stdin)
base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
if not base:
    print(json.dumps({"output": ""}))
    raise SystemExit(0)

request = urllib.request.Request(
    f"{base}/chat/completions",
    data=json.dumps({
        "model": os.environ.get("EVAL_LLM_MODEL", "alias-fast"),
        "messages": [{"role": "user", "content": case["input"]}],
        "temperature": 0,
        "max_tokens": 32,
        "stream": False,
    }).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")},
)
try:
    body = json.loads(urllib.request.urlopen(request, timeout=120).read())
    answer = (body["choices"][0]["message"]["content"] or "").strip()
except Exception as exc:  # noqa: BLE001
    answer = f"ERROR {type(exc).__name__}: {exc}"

print(json.dumps({"output": answer}))
