#!/usr/bin/env python3
"""An OpenAI-compatible reverse proxy that counts tokens.

Why this exists: DeepEval has no cost metric and no latency metric, and an
agent's token usage is invisible from outside the process that makes the call.
Putting a counting point on the wire makes usage observable without asking the
agent's author to instrument anything. The agent sets OPENAI_BASE_URL to this
proxy and changes nothing else.

The proxy is part of the harness, not part of any infrastructure, so the
measurement travels with the evaluation rather than requiring a particular
gateway. Point it at Blablador, at a local vLLM, or at a DESY gateway; the
numbers are produced the same way.

Configured entirely by environment, with names that cannot collide with the
project's own LLM configuration:

    EVAL_LLM_BASE_URL      full upstream URL, forwarded unchanged
    EVAL_LLM_API_KEY       credential for the upstream
    EVAL_LLM_AUTH_HEADER   header carrying it; default Authorization
    EVAL_LLM_MODEL         advisory; recorded, never substituted

There is deliberately no fallback to LLM_BASE_URL, LOCAL_LLM_* or OPENAI_*.
A fallback chain would reintroduce exactly the collision these names avoid.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_LOCK = threading.Lock()
CALLS: list[dict[str, Any]] = []


def _upstream_headers() -> dict[str, str]:
    """Auth header name is configurable because it is not always Authorization.

    Open WebUI behind a reverse proxy that already consumes Authorization needs
    x-api-key instead, and a gateway may want its own header. Hardcoding
    "Authorization: Bearer" fails silently against both.
    """
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("EVAL_LLM_API_KEY", "")
    if key:
        name = os.environ.get("EVAL_LLM_AUTH_HEADER", "Authorization")
        headers[name] = f"Bearer {key}" if name.lower() == "authorization" else key
    return headers


def _upstream_url(path: str) -> str:
    """Forward the configured URL as given.

    Open WebUI serves /api/chat/completions, not /v1/chat/completions.
    Appending a path we assume is the single most likely way to produce a 405
    against a perfectly working endpoint, so we do not assume one: if the
    configured URL already names an endpoint, it is used verbatim.
    """
    base = os.environ.get("EVAL_LLM_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("EVAL_LLM_BASE_URL is not set")
    if base.endswith(("/completions", "/embeddings", "/responses")):
        return base
    return f"{base}{path}"


def _want_usage(body: dict[str, Any]) -> dict[str, Any]:
    """Streaming omits usage unless asked. Ask, every time.

    Without stream_options an OpenAI-compatible stream returns no usage block
    at all, and the call would be reported as zero tokens rather than as
    unmeasured. Zero is a lie; unmeasured is the truth.
    """
    if body.get("stream"):
        opts = dict(body.get("stream_options") or {})
        opts["include_usage"] = True
        body["stream_options"] = opts
    return body


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:  # noqa: D102 - quiet by default
        pass

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        model = str(body.get("model") or os.environ.get("EVAL_LLM_MODEL") or "")
        payload = json.dumps(_want_usage(body)).encode("utf-8")

        started = time.perf_counter()
        status, out, usage, error = 0, b"", None, None
        try:
            req = urllib.request.Request(
                _upstream_url(self.path), data=payload,
                headers=_upstream_headers(), method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                status, out = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, out, error = exc.code, exc.read(), f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - reported, never raised at the agent
            status, out, error = 502, json.dumps(
                {"error": {"message": str(exc)}}).encode(), type(exc).__name__

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            usage = (json.loads(out.decode("utf-8")) or {}).get("usage")
        except Exception:  # noqa: BLE001 - streamed or non-JSON; usage stays None
            usage = None

        with _LOCK:
            CALLS.append({
                "path": self.path, "model": model, "status": status,
                "latency_ms": elapsed_ms, "error": error,
                "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": (usage or {}).get("completion_tokens"),
                "total_tokens": (usage or {}).get("total_tokens"),
                # A call whose usage we could not read is unmeasured, not free.
                "provenance": "measured" if usage else "not_available",
            })

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the proxy on a background thread; return it and its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def summary() -> dict[str, Any]:
    with _LOCK:
        calls = list(CALLS)
    measured = [c for c in calls if c["provenance"] == "measured"]
    total = sum(c["total_tokens"] or 0 for c in measured)
    return {
        "calls": len(calls),
        "calls_with_usage": len(measured),
        "total_tokens": total if measured else None,
        "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in measured) or None,
        "completion_tokens": sum(c["completion_tokens"] or 0 for c in measured) or None,
        "wall_ms": round(sum(c["latency_ms"] for c in calls), 1),
        "provenance": ("measured" if measured and len(measured) == len(calls)
                       else "partial" if measured else "not_available"),
        "note": (None if not calls else
                 "some calls returned no usage block; totals cover only those that did"
                 if len(measured) != len(calls) else None),
        "calls_detail": calls,
    }


if __name__ == "__main__":
    srv, url = start(int(os.environ.get("EVAL_PROXY_PORT", "0")))
    print(url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
