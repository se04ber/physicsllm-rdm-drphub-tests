"""A dependency-light MCP Streamable HTTP client, with a span per call.

Stdlib only for the transport. `urllib` is enough for JSON-RPC over HTTP,
and a card that needs no wheels to reach the server is a card that still
runs when the job has no outbound PyPI access.

Three things this does that a smaller client would skip, each for a reason:

* The full handshake. `initialize`, then the `notifications/initialized`
  note, then the session id from the `Mcp-Session-Id` response header on
  every later request. A server that issues a session id is entitled to
  reject requests that omit it, so the id is carried rather than dropped.

* A record for every call: wall-clock latency in milliseconds, the tool
  name, whether it succeeded, and the size of the response in bytes. That
  record exists whether or not OpenTelemetry is installed, so measurement
  never depends on an optional wheel.

* One OpenTelemetry span per call, named `execute_tool`, following the
  convention this project already uses in
  `services/api/src/c5/services/rdm/observability/instrument.py`. The span
  name and the two `gen_ai.*` attributes are the same there and here on
  purpose: one query reads both sides of the system.

No `gen_ai.usage.*` attributes are set anywhere in this file, and none
should be. These calls reach an MCP server, not a model. There are no
prompt tokens and no completion tokens to report, and inventing a zero
would be a measurement claim about something that never happened.
"""

from __future__ import annotations

import json
import ssl
import statistics
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

#: The OpenTelemetry GenAI semantic conventions this file was written
#: against, pinned for the same reason instrument.py pins it: every
#: gen_ai.* attribute is Development status, so the names can change and
#: this should be one edit rather than a search.
SEMCONV_VERSION = "1.38.0"

_OPERATION = "gen_ai.operation.name"
_TOOL_NAME = "gen_ai.tool.name"

#: Attributes of our own. Namespaced so nothing here can be mistaken for a
#: semantic convention that does not exist.
_METHOD = "rdm.mcp.method"
_BYTES = "rdm.mcp.response_bytes"
_STATUS = "rdm.mcp.http_status"
_OK = "rdm.mcp.ok"

#: The protocol revision this client speaks. Sent in `initialize`; a server
#: answering with a different one is recorded rather than argued with.
PROTOCOL_VERSION = "2025-06-18"

#: The nine read-only tools this server is documented to expose, from
#: services/mcp/src/rdm_mcp/server.py in the physics-llm-rdm repository.
#: Every one is annotated read_only_hint=true there. Held here so the card
#: can report the difference between what is documented and what the live
#: server actually advertises, rather than assuming they agree.
DOCUMENTED_TOOLS = (
    "panosc_search",
    "panosc_get_dataset",
    "resolve_technique",
    "find_schema_for",
    "list_schemas",
    "describe_schema",
    "validate_draft",
    "list_mappings",
    "describe_mapping",
)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------


class Telemetry:
    """A tracer plus the exporter holding what it produced.

    OpenTelemetry is optional here. When the SDK is missing, `enabled` is
    False, every span becomes a no-op, and the call records in `MCPClient`
    still carry the latencies. A card must produce its measurement whether
    or not an install step succeeded.

    The exporter is in-memory because the job has no collector to send to.
    Spans are read back at the end of the run and written to a file next to
    the DeepEval report.
    """

    def __init__(self, service_name: str = "drphub-mcp-observed") -> None:
        self.enabled = False
        self.error: str | None = None
        self._exporter = None
        self._provider = None
        self._tracer = None
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                InMemorySpanExporter,
            )
        except Exception as exc:  # noqa: BLE001 - absence is a reported state
            self.error = f"{type(exc).__name__}: {exc}"
            return

        self._exporter = InMemorySpanExporter()
        # A private provider rather than the global one. set_tracer_provider
        # takes effect once per process, so a second run inside the same
        # interpreter would silently read an empty exporter.
        self._provider = TracerProvider(
            resource=Resource.create(
                {"service.name": service_name, "telemetry.semconv.version": SEMCONV_VERSION}
            )
        )
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._tracer = self._provider.get_tracer(__name__)
        self.enabled = True

    def span(self, name: str):
        """A context manager for one span, or a no-op that behaves like one."""
        if not self.enabled:
            return _NullSpan()
        return _RealSpan(self._tracer, name)

    def finished_spans(self) -> list[dict[str, Any]]:
        """Every span exported so far, as plain dictionaries."""
        if not self.enabled:
            return []
        self._provider.force_flush()
        out = []
        for span in self._exporter.get_finished_spans():
            start, end = span.start_time, span.end_time
            out.append(
                {
                    "name": span.name,
                    "attributes": dict(span.attributes or {}),
                    "duration_ms": round((end - start) / 1_000_000, 3) if start and end else None,
                    "status": span.status.status_code.name if span.status else None,
                }
            )
        return out


class _NullSpan:
    """What `Telemetry.span` yields when the SDK is absent."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def set(self, _key: str, _value: Any) -> None:
        return None

    def fail(self, _description: str) -> None:
        return None


class _RealSpan:
    def __init__(self, tracer: Any, name: str) -> None:
        self._tracer = tracer
        self._name = name
        self._cm = None
        self._span = None

    def __enter__(self):
        from opentelemetry.trace import SpanKind

        self._cm = self._tracer.start_as_current_span(self._name, kind=SpanKind.CLIENT)
        self._span = self._cm.__enter__()
        return self

    def __exit__(self, *exc) -> bool:
        return bool(self._cm.__exit__(*exc))

    def set(self, key: str, value: Any) -> None:
        # Telemetry is evidence, never control flow. The same rule
        # instrument.py follows: a span is not worth failing a call for.
        try:
            self._span.set_attribute(key, value)
        except Exception:  # noqa: BLE001
            pass

    def fail(self, description: str) -> None:
        try:
            from opentelemetry.trace import Status, StatusCode

            self._span.set_status(Status(StatusCode.ERROR, description[:200]))
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def tls_context(tls_mode: str) -> ssl.SSLContext:
    """A verifying context, or a deliberately non-verifying one.

    `insecure` is the default the card ships with because the server has
    been serving a self-signed certificate. Flip the `tls_mode` parameter
    in reana.yaml to `verify` once the official certificate lands, and a
    green run then proves the path end to end with no caveat attached.
    """
    context = ssl.create_default_context()
    if tls_mode == "insecure":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


class CallRecord(dict):
    """One measured call. A dict so it serialises with no help."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


class MCPClient:
    """Streamable HTTP MCP client that measures every call it makes."""

    def __init__(
        self,
        base: str,
        token: str = "",
        *,
        tls_mode: str = "insecure",
        timeout: float = 90.0,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.endpoint = base.rstrip("/") + "/mcp"
        self.base = base.rstrip("/")
        self.token = (token or "").strip()
        self.tls_mode = tls_mode
        self.timeout = timeout
        self.telemetry = telemetry or Telemetry()
        self._context = tls_context(tls_mode)
        self._session_id: str | None = None
        self._rid = 0
        self.records: list[CallRecord] = []
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}

    # -- plumbing ----------------------------------------------------------

    def _next_id(self) -> int:
        self._rid += 1
        return self._rid

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Streamable HTTP servers may answer with either, so accept both.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _decode(raw: str) -> tuple[dict[str, Any] | None, str | None]:
        """JSON-RPC body out of a plain JSON or an SSE response."""
        text = raw
        if raw.lstrip().startswith("event:") or "\ndata:" in raw:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    text = line[5:].strip()
                    break
        if not text.strip():
            return None, None
        try:
            return json.loads(text), None
        except json.JSONDecodeError:
            return None, f"non-JSON reply: {text[:200]}"

    def _post(self, payload: dict[str, Any], *, tool: str | None, method: str) -> CallRecord:
        """One HTTP round trip, measured and traced.

        Every exit from this function appends exactly one record, so the
        count of records is the count of calls attempted and a failure is
        never invisible in the summary.
        """
        body = json.dumps(payload).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST")
        for key, value in self._headers().items():
            request.add_header(key, value)

        record = CallRecord(
            method=method,
            tool=tool,
            ok=False,
            latency_ms=None,
            response_bytes=0,
            http_status=None,
            error=None,
        )

        with self.telemetry.span("execute_tool") as span:
            span.set(_OPERATION, "execute_tool")
            span.set(_METHOD, method)
            if tool:
                span.set(_TOOL_NAME, tool)

            started = time.perf_counter()
            raw = ""
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._context
                ) as handle:
                    raw = handle.read().decode("utf-8", "replace")
                    record["http_status"] = handle.status
                    session = handle.headers.get("Mcp-Session-Id")
                    if session:
                        self._session_id = session
            except urllib.error.HTTPError as exc:
                record["http_status"] = exc.code
                detail = exc.read().decode("utf-8", "replace")[:300]
                record["error"] = f"HTTP {exc.code}: {detail}"
            except OSError as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"

            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            record["response_bytes"] = len(raw.encode("utf-8"))

            if record["error"] is None:
                decoded, problem = self._decode(raw)
                if problem:
                    record["error"] = problem
                elif decoded is not None and "error" in decoded:
                    rpc = decoded["error"] or {}
                    record["error"] = f"JSON-RPC {rpc.get('code')}: {rpc.get('message')}"
                    record["body"] = decoded
                else:
                    record["ok"] = True
                    record["body"] = decoded

            span.set(_BYTES, record["response_bytes"])
            span.set(_OK, record["ok"])
            if record["http_status"] is not None:
                span.set(_STATUS, record["http_status"])
            if not record["ok"]:
                span.fail(str(record["error"]))

        self.records.append(record)
        return record

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> CallRecord:
        """A JSON-RPC notification: no id, and no result to wait for."""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload, tool=None, method=method)

    # -- protocol ----------------------------------------------------------

    def initialize(self) -> CallRecord:
        """Handshake, then the initialized note the spec requires."""
        record = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "drphub-mcp-observed", "version": "1"},
                },
            },
            tool=None,
            method="initialize",
        )
        if record.ok:
            result = (record.get("body") or {}).get("result") or {}
            self.protocol_version = result.get("protocolVersion")
            self.server_info = result.get("serverInfo") or {}
            # A notification, so a non-2xx here is recorded and the session
            # carries on. Servers differ on whether they answer it at all.
            self._notify("notifications/initialized")
        return record

    def list_tools(self) -> tuple[list[dict[str, Any]], CallRecord]:
        record = self._post(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}},
            tool=None,
            method="tools/list",
        )
        if not record.ok:
            return [], record
        result = (record.get("body") or {}).get("result") or {}
        return list(result.get("tools") or []), record

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallRecord:
        record = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            tool=name,
            method="tools/call",
        )
        if record.ok:
            result = (record.get("body") or {}).get("result") or {}
            # An MCP tool reports its own failure inside a successful
            # JSON-RPC response. A refusal is a real answer from the server,
            # so it is kept separate from a transport failure.
            if result.get("isError"):
                record["ok"] = False
                record["tool_error"] = True
                record["error"] = f"tool error: {_text_of(result)[:300]}"
            else:
                record["value"] = unwrap(result)
        return record

    # -- measurement -------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Per-tool counts and latencies, plus the total wall time.

        Keyed by tool name for `tools/call`, and by method name for the
        protocol calls, so the handshake is visible rather than folded into
        an average it does not belong in.
        """
        buckets: dict[str, list[CallRecord]] = {}
        for record in self.records:
            buckets.setdefault(record.get("tool") or record.get("method"), []).append(record)

        per_tool = {}
        for key, group in sorted(buckets.items()):
            latencies = [r["latency_ms"] for r in group if r.get("latency_ms") is not None]
            per_tool[key] = {
                "calls": len(group),
                "ok": sum(1 for r in group if r.ok),
                "failed": sum(1 for r in group if not r.ok),
                "mean_latency_ms": round(statistics.fmean(latencies), 3) if latencies else None,
                # Population standard deviation, and None for a single call.
                # A sample stdev of one call is undefined, and reporting 0.0
                # would read as "no spread was observed" rather than "one
                # call cannot have spread".
                "stdev_latency_ms": (
                    round(statistics.pstdev(latencies), 3) if len(latencies) > 1 else None
                ),
                "min_latency_ms": round(min(latencies), 3) if latencies else None,
                "max_latency_ms": round(max(latencies), 3) if latencies else None,
                "total_response_bytes": sum(r.get("response_bytes") or 0 for r in group),
            }

        total = sum(r["latency_ms"] for r in self.records if r.get("latency_ms") is not None)
        return {
            "semconv_version": SEMCONV_VERSION,
            "opentelemetry": {
                "available": self.telemetry.enabled,
                "error": self.telemetry.error,
                "exporter": "in-memory (the job has no collector to send to)",
            },
            "span_name": "execute_tool",
            "gen_ai_usage": (
                "unset on purpose: these calls reach an MCP server and no model, "
                "so there are no tokens to report"
            ),
            "total_calls": len(self.records),
            "total_wall_ms": round(total, 3),
            "per_tool": per_tool,
        }


def _text_of(result: dict[str, Any]) -> str:
    return " ".join(
        str(block.get("text") or "")
        for block in (result.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def unwrap(result: dict[str, Any]) -> Any:
    """The tool's own return value out of the MCP result envelope.

    `structuredContent` first because it is the typed form and needs no
    parsing. Falling back to the text block covers a server that ships only
    the human-readable rendering. A text block that is not JSON is returned
    as the string it is, which is a true answer about what came back.
    """
    if "structuredContent" in result:
        structured = result["structuredContent"]
        # MCP wraps a non-object return in {"result": ...} so the field can
        # be typed. Unwrap that one level, and leave a real object alone.
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    text = _text_of(result)
    if not text:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def new_run_id() -> str:
    """A run id: REANA's when there is one, otherwise a local stamp."""
    import datetime
    import os

    return (
        os.environ.get("REANA_WORKFLOW_UUID")
        or datetime.datetime.now(datetime.timezone.utc).strftime("local-%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:6]
    )
