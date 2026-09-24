#!/usr/bin/env python3
"""Ask the RDM MCP server 24 questions, score the answers, time every call.

    python3 run.py --base https://physicsllm-rdm.desy.de:8443

Each case in cases/cases.jsonl names a tool, its arguments, which part of
the reply is the answer, and what the answer should be. Goldens are fixed
by files the server itself reads, so a failed case is a regression in the
deployment, not a matter of opinion.

Scoring uses DeepEval's three no-judge metrics (ExactMatch, PatternMatch,
JsonCorrectness) when DeepEval is installed, and the same comparison in
plain Python when it is not. Every case records which one produced its
verdict. Latency comes from one OpenTelemetry span per call, and from a
plain timer when the SDK is absent, so the numbers never depend on an
install step.

The gate has three values. A case with no answer was never asked, so it is
neither a pass nor a failure. Without a token every case is in that state
and a report is still written. Always exits 0.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mcp_client import DOCUMENTED_TOOLS, MCPClient, Telemetry, new_run_id  # noqa: E402


class Scorer:
    """The three no-judge metrics, each with a plain-Python twin."""

    def __init__(self) -> None:
        self.available, self.error = False, None
        try:
            from deepeval.metrics import ExactMatchMetric, JsonCorrectnessMetric, PatternMatchMetric
            from deepeval.test_case import LLMTestCase
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return
        self._exact, self._pattern, self._json, self._case = (
            ExactMatchMetric, PatternMatchMetric, JsonCorrectnessMetric, LLMTestCase)
        self.available = True

    def exact(self, actual: str, expected: str) -> tuple[bool, str]:
        if not self.available:
            return actual.strip() == expected.strip(), "computed locally"
        m = self._exact()
        m.measure(self._case(input="mcp", actual_output=actual, expected_output=expected), _show_indicator=False)
        return m.is_successful(), "deepeval:ExactMatchMetric"

    def pattern(self, actual: str, pattern: str) -> tuple[bool, str]:
        if not self.available:
            return re.fullmatch(pattern, actual.strip()) is not None, "computed locally"
        m = self._pattern(pattern=pattern)
        m.measure(self._case(input="mcp", actual_output=actual), _show_indicator=False)
        return m.is_successful(), "deepeval:PatternMatchMetric"

    def json_fields(self, actual: str, required: list[str], optional: list[str]) -> tuple[bool, str]:
        # A pydantic field must be an identifier, so a key such as `$schema`
        # is checked directly. The report says which path produced the verdict.
        if self.available and all(n.isidentifier() for n in [*required, *optional]):
            from pydantic import create_model
            fields: dict[str, Any] = {n: (Any, ...) for n in required}
            fields.update({n: (Any, None) for n in optional})
            model = create_model("Expected", **fields)
            m = self._json(expected_schema=model, model=_no_judge(), include_reason=False, async_mode=False)
            m.measure(self._case(input="mcp", actual_output=actual), _show_indicator=False)
            return m.is_successful(), "deepeval:JsonCorrectnessMetric"
        try:
            parsed = json.loads(actual)
        except json.JSONDecodeError:
            return False, "computed locally"
        return isinstance(parsed, dict) and all(n in parsed for n in required), "computed locally"


def _no_judge():
    """JsonCorrectnessMetric wants a model object at construction and, with
    include_reason=False, never calls it. This one raises if that changes."""
    from deepeval.models import DeepEvalBaseLLM

    class NoJudge(DeepEvalBaseLLM):
        def __init__(self) -> None:
            return None

        def load_model(self) -> None:
            return None

        def generate(self, *_a, **_k):
            raise RuntimeError("this card has no model endpoint")

        async def a_generate(self, *_a, **_k):
            raise RuntimeError("this card has no model endpoint")

        def get_model_name(self) -> str:
            return "none"

    return NoJudge()


_MISSING = object()


def extract(value: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.lstrip("-").isdigit() and -len(value) <= int(part) < len(value):
            value = value[int(part)]
        else:
            return _MISSING
    return value


def render(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)


def answer(client: MCPClient, row: dict) -> tuple[str, dict | None, str | None]:
    """Returns (actual_output, call record, reason it was not answered)."""
    meta = row.get("additional_metadata") or {}
    tool = meta.get("tool")
    if tool not in DOCUMENTED_TOOLS:
        return "", None, f"{tool!r} is not one of the nine documented tools"
    rec = client.call_tool(tool, meta.get("arguments") or {})
    call = {"tool": tool, "ok": rec.ok, "latency_ms": rec.get("latency_ms"),
            "http_status": rec.get("http_status"), "error": rec.get("error")}
    if meta.get("observe") == "call_outcome":
        return ("ok" if rec.ok else "error"), call, None
    if not rec.ok:
        return "", call, f"call failed: {rec.get('error')}"
    found = extract(rec.get("value"), meta.get("extract") or "")
    return ("null" if found is _MISSING else render(found)), call, None


def score(scorer: Scorer, row: dict, actual: str) -> dict:
    meta = row.get("additional_metadata") or {}
    metric = meta.get("metric") or "exact_match"
    try:
        if metric == "reference_free":
            return {"metric": metric, "scored": False, "pass": None,
                    "reason": meta.get("reference_free_reason") or "no expected value can be stated"}
        if metric == "pattern_match":
            ok, by = scorer.pattern(actual, meta.get("pattern") or "")
        elif metric == "json_correctness":
            ok, by = scorer.json_fields(actual, list(meta.get("json_required") or []),
                                        list(meta.get("json_optional") or []))
        else:
            ok, by = scorer.exact(actual, row.get("expected_output") or "")
        return {"metric": metric, "scored": True, "pass": ok, "computed_by": by}
    except Exception as exc:  # noqa: BLE001
        return {"metric": metric, "scored": False, "pass": None, "reason": f"metric raised: {exc}"}


def surface(client: MCPClient) -> dict:
    tools, rec = client.list_tools()
    if not rec.ok:
        return {"checked": False, "verdict": f"tools/list failed: {rec.get('error')}"}
    names = sorted(str(t.get("name") or "") for t in tools)
    missing = sorted(set(DOCUMENTED_TOOLS) - set(names))
    extra = sorted(set(names) - set(DOCUMENTED_TOOLS))
    not_ro = sorted(n for n, t in zip(names, sorted(tools, key=lambda t: str(t.get("name"))))
                    if not (t.get("annotations") or {}).get("readOnlyHint"))
    ok = not missing and not extra and not not_ro
    return {"checked": True, "advertised": names, "missing": missing, "extra": extra,
            "not_read_only": not_ro,
            "verdict": "matches the nine documented read-only tools" if ok
            else f"missing={missing} extra={extra} not_read_only={not_ro}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("MCP_BASE", "https://physicsllm-rdm.desy.de:8443"))
    ap.add_argument("--tls", choices=("verify", "insecure"), default="verify")
    ap.add_argument("--cases", default=str(pathlib.Path(__file__).resolve().parent / "cases" / "cases.jsonl"))
    ap.add_argument("--out", default="results")
    ap.add_argument("--timeout", type=float, default=90.0)
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    token = (os.environ.get("RDM_MCP_BEARER_TOKEN") or "").strip()
    telemetry, scorer = Telemetry("mcp-tools"), Scorer()
    rows = [json.loads(l) for l in pathlib.Path(a.cases).read_text(encoding="utf-8").splitlines() if l.strip()]

    report: dict[str, Any] = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "run_id": new_run_id(), "endpoint": a.base.rstrip("/") + "/mcp", "tls": a.tls,
        "token_supplied": bool(token),
        "deepeval": {"available": scorer.available, "error": scorer.error},
        "opentelemetry": {"available": telemetry.enabled, "error": telemetry.error},
    }

    client = MCPClient(a.base, token, tls_mode=a.tls, timeout=a.timeout, telemetry=telemetry) if token else None
    session = None
    if client is None:
        report["session"] = "no token: set RDM_MCP_BEARER_TOKEN"
    else:
        hs = client.initialize()
        if hs.ok:
            session = client
            report["session"] = "established"
            report["server"] = client.server_info
        else:
            report["session"] = f"handshake failed: {hs.get('error')}"
    report["tool_surface"] = surface(session) if session else {"checked": False, "verdict": "not checked, no session"}

    cases = []
    for row in rows:
        actual, call, not_run = answer(session, row) if session else ("", None, report["session"])
        case = {"name": row.get("name"), "tool": (row.get("additional_metadata") or {}).get("tool"),
                "expected": row.get("expected_output") or "", "actual": actual, "call": call}
        case.update({"scored": False, "pass": None, "reason": not_run} if not actual.strip()
                    else score(scorer, row, actual))
        cases.append(case)
    scored = [c for c in cases if c["scored"]]
    failed = [c["name"] for c in scored if not c["pass"]]
    report["cases"] = cases
    report["summary"] = {"cases": len(cases), "scored": len(scored), "failed": failed,
                         "gate": "not scored" if not scored else "fail" if failed else "pass"}
    otel = client.summary() if client else {"total_calls": 0, "total_wall_ms": 0.0, "per_tool": {}}
    spans = telemetry.finished_spans()

    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "otel_summary.json").write_text(json.dumps(otel, indent=2) + "\n", encoding="utf-8")
    (out / "otel_spans.json").write_text(json.dumps(spans, indent=2) + "\n", encoding="utf-8")

    print(f"\nmcp-tools   {report['endpoint']}   tls={a.tls}")
    print(f"  session   {report['session']}")
    print(f"  tools     {report['tool_surface']['verdict']}")
    print(f"  deepeval  {'on' if scorer.available else 'off'}   opentelemetry {'on' if telemetry.enabled else 'off'}   spans {len(spans)}\n")
    for c in cases:
        mark = "--  " if not c["scored"] else "ok  " if c["pass"] else "FAIL"
        ms = (c["call"] or {}).get("latency_ms")
        print(f"  {mark} {str(c['name'])[:50]:<52}{(f'{ms:7.1f} ms' if isinstance(ms, (int, float)) else ''):>10}")
    by_tool = collections.Counter(c["tool"] for c in scored)
    s = report["summary"]
    print(f"\n  {s['scored']}/{s['cases']} scored across {len(by_tool)} tools, {len(failed)} failed, "
          f"{otel['total_calls']} calls in {otel['total_wall_ms']:.0f} ms")
    print(f"  gate      {s['gate']}\n\n  {out / 'report.json'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
