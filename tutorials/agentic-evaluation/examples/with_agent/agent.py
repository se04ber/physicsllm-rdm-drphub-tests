#!/usr/bin/env python3
"""The smallest system the harness can measure.

Contract: one case as JSON on stdin, one JSON object on stdout with an
"output" key. Replace the body with your own agent and change nothing else.

OPENAI_BASE_URL is set by the harness to a counting proxy running beside this
process, so model calls are counted without this file knowing where the model
actually is. That indirection is the whole reason token measurement needs no
cooperation from the system under test.
"""
import json
import os
import sys
import urllib.request

case = json.load(sys.stdin)

base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
if not base:
    # No proxy configured: answer without a model rather than crash, so the
    # correctness half still runs.
    print(json.dumps({"output": ""}))
    raise SystemExit(0)

request = urllib.request.Request(
    f"{base}/chat/completions",
    data=json.dumps({
        "model": os.environ.get("EVAL_LLM_MODEL", "alias-fast"),
        "messages": [{
            "role": "user",
            "content": f"{case['input']}\n\nAnswer with the value only.",
        }],
        "temperature": 0,
        "max_tokens": 32,
        # Explicitly unstreamed: this agent parses one JSON document, and a
        # streamed reply is a sequence of data: frames instead. The counting
        # proxy handles either, so an agent that does stream still gets its
        # tokens counted; this one simply has no reason to.
        "stream": False,
    }).encode(),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", ""),
    },
)

try:
    body = json.loads(urllib.request.urlopen(request, timeout=120).read())
    answer = (body["choices"][0]["message"]["content"] or "").strip()
except Exception as exc:  # noqa: BLE001
    # Returned as the answer, never raised: one broken call should not lose
    # the whole run, and the harness records it as a per-case error.
    answer = f"ERROR {type(exc).__name__}: {exc}"

print(json.dumps({"output": answer}))
