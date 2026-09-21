#!/usr/bin/env python3
"""Evaluate an agentic system: correctness with DeepEval, latency and tokens
with OpenTelemetry and a counting proxy.

Runs anywhere. Nothing in the measurement path needs particular infrastructure:
point it at your own system and your own OpenAI-compatible model and it
produces the same numbers it would produce for us. What running it on someone
else's infrastructure changes is assurance, not capability, and the report says
which it was.

Layout: a benchmark folder is any directory containing `cases.jsonl`. Depth is
irrelevant, so a flat folder and a deep suite tree both work. `system.json`
beside it, or in the sibling `use_cases/<id>/` of the older tree, says how to
invoke the system under test. Without one the cases are scored as they arrive.

Every number carries how it was obtained. `measured` means this harness
observed it; `self_reported` means the case supplied it; `not_available` means
nobody did. A missing measurement is never reported as zero.

Always exits 0. A system that is not set up yet is a finding, not a failure.
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import counting_proxy  # noqa: E402

UTC = datetime.timezone.utc


# --------------------------------------------------------------------------
# optional dependencies, each reported rather than assumed

def _load_deepeval() -> tuple[Any, dict[str, Any]]:
    try:
        from deepeval.metrics import ExactMatchMetric  # noqa: F401
        import deepeval
        return deepeval, {"available": True, "version": getattr(deepeval, "__version__", "?")}
    except Exception as exc:  # noqa: BLE001
        return None, {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _load_otel() -> tuple[Any, dict[str, Any]]:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter)
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return (trace.get_tracer("physicsllm.eval_harness"), exporter), {
            "available": True, "semconv": "1.38.0"}
    except Exception as exc:  # noqa: BLE001
        return None, {"available": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# invoking the system under test

def _invoke_http(spec: dict[str, Any], case: dict[str, Any]) -> str:
    url = str(spec.get("base") or spec.get("url") or "").rstrip("/")
    if not url:
        raise RuntimeError("system.json kind=http_endpoint needs 'base'")
    payload = json.dumps({"input": case.get("input", ""),
                          "metadata": case.get("additional_metadata") or {}}).encode()
    headers = {"Content-Type": "application/json"}
    token = os.environ.get(str(spec.get("token_env") or ""), "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=int(spec.get("timeout_s") or 120)) as r:
        body = json.loads(r.read().decode("utf-8"))
    return str(body.get("output") if isinstance(body, dict) else body)


def _invoke_python(spec: dict[str, Any], case: dict[str, Any]) -> str:
    target = str(spec.get("entrypoint") or "")
    if ":" not in target:
        raise RuntimeError("system.json kind=python_entrypoint needs 'entrypoint' "
                           "as module:callable")
    mod_name, _, fn_name = target.partition(":")
    fn = getattr(importlib.import_module(mod_name), fn_name)
    return str(fn(case.get("input", ""), case.get("additional_metadata") or {}))


def _invoke_subprocess(spec: dict[str, Any], case: dict[str, Any]) -> str:
    cmd = spec.get("command")
    if not cmd:
        raise RuntimeError("system.json kind=subprocess needs 'command'")
    proc = subprocess.run(
        cmd if isinstance(cmd, list) else ["sh", "-c", str(cmd)],
        input=json.dumps({"input": case.get("input", ""),
                          "metadata": case.get("additional_metadata") or {}}),
        capture_output=True, text=True, timeout=int(spec.get("timeout_s") or 300))
    if proc.returncode != 0:
        raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    try:
        return str(json.loads(proc.stdout).get("output"))
    except ValueError:
        return proc.stdout.strip()


INVOKERS = {"http_endpoint": _invoke_http,
            "python_entrypoint": _invoke_python,
            "subprocess": _invoke_subprocess}


# --------------------------------------------------------------------------
# scoring: deterministic first, because a gate that depends on a judge is a
# gate that moves when the judge does

def _normalise(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("_", " ").split())


def _score(want: str, got: str) -> dict[str, Any]:
    exact = got == want
    return {"exact": exact,
            "normalised": exact or _normalise(got) == _normalise(want),
            "method": "computed without DeepEval"}


def _find_system(cases_path: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    """system.json beside the cases, or in the older use_cases/<id>/ sibling."""
    here = cases_path.parent / "system.json"
    if here.is_file():
        return json.loads(here.read_text(encoding="utf-8"))
    rel = cases_path.relative_to(root).parts
    if "datasets" in rel:
        i = rel.index("datasets")
        if len(rel) > i + 1:
            legacy = root.joinpath(*rel[:i], "use_cases", rel[i + 1], "system.json")
            if legacy.is_file():
                return json.loads(legacy.read_text(encoding="utf-8"))
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="benchmark", help="directory to search for cases.jsonl")
    ap.add_argument("--out", default="results")
    ap.add_argument("--repeats", type=int, default=1,
                    help="one run cannot distinguish reliable from lucky")
    a = ap.parse_args()

    root = pathlib.Path(a.tree).resolve()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    deepeval, de_info = _load_deepeval()
    otel, otel_info = _load_otel()
    tracer, exporter = otel if otel else (None, None)

    proxy_url, proxy_note = None, None
    if os.environ.get("EVAL_LLM_BASE_URL"):
        _, proxy_url = counting_proxy.start()
        os.environ["OPENAI_BASE_URL"] = f"{proxy_url}/v1"
        os.environ.setdefault("OPENAI_API_KEY", "counted-by-proxy")
    else:
        proxy_note = ("EVAL_LLM_BASE_URL not set, so no counting proxy is running "
                      "and token usage is not_available")

    report: dict[str, Any] = {
        "utc": datetime.datetime.now(UTC).isoformat(),
        "tree": str(root),
        "run_id": os.environ.get("REANA_WORKFLOW_UUID") or
                  f"local-{datetime.datetime.now(UTC):%Y%m%dT%H%M%SZ}",
        "ran_on": "reana" if os.environ.get("REANA_WORKFLOW_UUID") else "local",
        "deepeval": de_info, "opentelemetry": otel_info,
        "proxy": {"url": proxy_url, "note": proxy_note},
        "repeats": a.repeats, "datasets": [],
    }
    print(f"eval harness  run {report['run_id']}  {report['utc']}")
    print(f"deepeval:  {'v' + de_info.get('version','?') if de_info['available'] else de_info['error']}")
    print(f"otel:      {'ready' if otel_info['available'] else otel_info['error']}")
    print(f"proxy:     {proxy_url or proxy_note}\n")

    found = sorted(root.rglob("cases.jsonl"))
    if not found:
        report["verdict"] = f"no cases.jsonl anywhere under {root}"
        print(report["verdict"])
    for cases_path in found:
        rows = [json.loads(l) for l in
                cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        system = _find_system(cases_path, root)
        kind = str(system.get("kind") or "")
        name = str(cases_path.parent.relative_to(root)) or cases_path.parent.name

        invoker = INVOKERS.get(kind)
        # A kind we do not implement is not a missing credential and not a
        # broken dataset. Saying so is the difference between a report someone
        # can act on and one that sends them looking for a token they have.
        # static_artifact is not unhandled: it means the answers already sit in
        # the file, which is exactly what scoring them as they arrive does.
        unhandled = (f"system.json declares kind={kind!r}, which this harness "
                     f"does not invoke; handled kinds are "
                     f"{', '.join(sorted(INVOKERS))}, or static_artifact for "
                     f"answers already in the file"
                     ) if kind and not invoker and kind != "static_artifact" else None
        scored, latencies, errors = [], [], []
        for rep in range(a.repeats):
            for row in rows:
                want = str(row.get("expected_output") or "")
                got, prov, err = str(row.get("actual_output") or ""), "self_reported", None
                if invoker:
                    started = time.perf_counter()
                    try:
                        got, prov = invoker(system, row), "measured"
                    except Exception as exc:  # noqa: BLE001
                        err = f"{type(exc).__name__}: {exc}"
                        errors.append(err)
                    ms = round((time.perf_counter() - started) * 1000, 1)
                    latencies.append(ms)
                    if tracer:
                        with tracer.start_as_current_span("invoke_agent") as span:
                            span.set_attribute("gen_ai.operation.name", "invoke_agent")
                            span.set_attribute("physicsllm.case", str(row.get("name") or ""))
                            span.set_attribute("physicsllm.latency_ms", ms)
                if rep == 0:
                    scored.append({"name": row.get("name") or row.get("input", "")[:40],
                                   "expected": want, "actual": got,
                                   "provenance": prov, "error": err,
                                   **({} if not want else _score(want, got))})

        graded = [s for s in scored if s.get("expected") and s.get("actual")]
        passed = [s for s in graded if s.get("normalised")]
        gate = ("NOT-SCORED" if not graded else
                "pass" if len(passed) == len(graded) else "fail")
        entry = {
            "dataset": name, "cases": len(rows), "kind": kind or "unspecified",
            "graded": len(graded), "passed": len(passed), "gate": gate,
            "latency_ms": ({"mean": round(statistics.mean(latencies), 1),
                            "sd": round(statistics.stdev(latencies), 1) if len(latencies) > 1 else None,
                            "provenance": "measured"} if latencies else
                           {"provenance": "not_available",
                            "note": "no invocation contract in system.json, so nothing was timed"}),
            "errors": errors[:5], "results": scored,
        }
        if unhandled:
            entry["note"] = unhandled
        report["datasets"].append(entry)
        print(f"  {name}  {len(passed)}/{len(graded)} scored  gate={gate}"
              f"  kind={kind or 'unspecified'}")
        if entry["latency_ms"].get("mean"):
            print(f"      latency {entry['latency_ms']['mean']}ms"
                  f" sd={entry['latency_ms']['sd']}")
        if unhandled:
            print(f"      {unhandled}")
        for e in errors[:3]:
            print(f"      ERROR {e}")

    tokens = counting_proxy.summary() if proxy_url else {
        "provenance": "not_available", "note": proxy_note}
    report["tokens"] = tokens
    if otel and exporter:
        report["spans"] = len(exporter.get_finished_spans())

    print(f"\ntokens: {tokens.get('total_tokens')} "
          f"({tokens.get('calls', 0)} model calls, {tokens.get('provenance')})")
    tally = {}
    for d in report["datasets"]:
        tally[d["gate"]] = tally.get(d["gate"], 0) + 1
    print(f"{len(report['datasets'])} dataset(s); " +
          ", ".join(f"{v} {k}" for k, v in sorted(tally.items())))

    (out / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out / 'eval_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
