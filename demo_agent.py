#!/usr/bin/env python3
"""A minimal agent, so the harness has something real to invoke.

It exists to exercise the measurement path end to end rather than to be
clever: one case in on stdin, one answer out on stdout, and the answer comes
from a model call so the counting proxy has something to count. Any submitted
system implements the same contract.

OPENAI_BASE_URL is set by the harness to its own proxy, so this code needs no
knowledge of where the model actually lives.
"""
import json, os, sys, urllib.request

case = json.load(sys.stdin)
base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
prompt = (f"{case['input']}\n\n"
          "Answer with the single value only, no sentence, no punctuation.")
body = json.dumps({"model": os.environ.get("EVAL_LLM_MODEL", "alias-fast"),
                   "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0, "max_tokens": 32,
                   # Unstreamed: this agent parses one JSON document. The
                   # counting proxy handles either shape, so an agent that
                   # does stream still gets counted.
                   "stream": False}).encode()
req = urllib.request.Request(f"{base}/chat/completions", data=body,
                             headers={"Content-Type": "application/json",
                                      "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")})
try:
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    out = (r["choices"][0]["message"]["content"] or "").strip()
except Exception as exc:  # noqa: BLE001 - reported as the answer, never raised
    out = f"ERROR {type(exc).__name__}: {exc}"
print(json.dumps({"output": out}))
