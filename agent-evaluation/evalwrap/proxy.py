"""An OpenAI-compatible reverse proxy that counts tokens.

The agent sets OPENAI_BASE_URL to this proxy and changes nothing else. Every
call's usage block is recorded on the way back. A call whose usage cannot be
read is reported as unmeasured, never as zero.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_LOCK = threading.Lock()
_CALLS: list[dict[str, Any]] = []
_CFG: dict[str, str] = {}


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key, name = _CFG.get("api_key", ""), _CFG.get("auth_header", "Authorization")
    if key:
        headers[name] = f"Bearer {key}" if name.lower() == "authorization" else key
    return headers


def _upstream(path: str) -> str:
    # Forwarded as configured. Open WebUI serves /api/chat/completions, so a
    # base URL that already names an endpoint is used verbatim.
    base = _CFG["base_url"].rstrip("/")
    return base if base.endswith(("/completions", "/embeddings", "/responses")) else base + path


def _usage(body: bytes) -> dict[str, Any] | None:
    text = body.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("usage"):
            return parsed["usage"]
    except ValueError:
        pass
    found = None
    for line in text.splitlines():  # streamed replies carry usage in a late frame
        line = line.strip()
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            try:
                frame = json.loads(line[5:])
            except ValueError:
                continue
            if isinstance(frame, dict) and frame.get("usage"):
                found = frame["usage"]
    return found


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        if body.get("stream"):  # streaming omits usage unless asked
            body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        model = str(body.get("model") or _CFG.get("model") or "")

        started = time.perf_counter()
        status, out, error = 0, b"", None
        try:
            req = urllib.request.Request(_upstream(self.path), data=json.dumps(body).encode(),
                                         headers=_headers(), method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                status, out = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, out, error = exc.code, exc.read(), f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            status, out, error = 502, json.dumps({"error": {"message": str(exc)}}).encode(), type(exc).__name__

        usage = _usage(out)
        with _LOCK:
            _CALLS.append({
                "model": model, "status": status, "error": error,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": (usage or {}).get("completion_tokens"),
                "total_tokens": (usage or {}).get("total_tokens"),
                "provenance": "measured" if usage else "not_available",
                **({} if usage else {"body_preview": " ".join(out[:300].decode("utf-8", "replace").split())[:200]}),
            })
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST


def start(base_url: str, api_key: str = "", auth_header: str = "Authorization",
          model: str = "") -> str:
    """Start the proxy on a background thread and return its base URL."""
    _CFG.update(base_url=base_url, api_key=api_key, auth_header=auth_header, model=model)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def summary() -> dict[str, Any]:
    with _LOCK:
        calls = list(_CALLS)
    measured = [c for c in calls if c["provenance"] == "measured"]
    return {
        "calls": len(calls),
        "calls_with_usage": len(measured),
        "total_tokens": sum(c["total_tokens"] or 0 for c in measured) if measured else None,
        "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in measured) or None,
        "completion_tokens": sum(c["completion_tokens"] or 0 for c in measured) or None,
        "provenance": ("measured" if measured and len(measured) == len(calls)
                       else "partial" if measured else "not_available"),
        "unparsed_example": next((c["body_preview"] for c in calls if c.get("body_preview")), None),
        "calls_detail": calls,
    }
