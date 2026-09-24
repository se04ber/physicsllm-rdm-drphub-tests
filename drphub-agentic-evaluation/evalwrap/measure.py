"""Run the agent over the dataset and measure it.

Correctness: DeepEval's exact match, plus a normalised match that ignores
case, underscores and spacing. Latency: one OpenTelemetry span per
invocation. Tokens: whatever passed through the counting proxy.

Every number says how it was obtained: measured, self_reported or
not_available. A missing measurement is never written as zero.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import statistics
import subprocess
import time
from typing import Any

from . import proxy

UTC = datetime.timezone.utc


def _load_deepeval() -> tuple[Any, dict[str, Any]]:
    try:
        import deepeval
        from deepeval.metrics import ExactMatchMetric
        from deepeval.test_case import LLMTestCase
        return (ExactMatchMetric, LLMTestCase), {"available": True, "version": getattr(deepeval, "__version__", "?")}
    except Exception as exc:  # noqa: BLE001
        return None, {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _load_otel() -> tuple[Any, dict[str, Any]]:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter, provider = InMemorySpanExporter(), TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return (trace.get_tracer("evalwrap"), exporter), {"available": True}
    except Exception as exc:  # noqa: BLE001
        return None, {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _normalise(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("_", " ").split())


def _score(want: str, got: str, deepeval: Any) -> dict[str, Any]:
    exact, method = got == want, "computed locally"
    if deepeval:
        try:
            metric_cls, case_cls = deepeval
            metric = metric_cls()
            metric.measure(case_cls(input="", actual_output=got, expected_output=want))
            exact, method = bool(metric.score), "DeepEval ExactMatchMetric"
        except Exception:  # noqa: BLE001
            pass
    return {"exact": exact, "normalised": exact or _normalise(got) == _normalise(want), "method": method}


def _invoke(agent: pathlib.Path, case: dict[str, Any], timeout: int) -> str:
    cmd = ["python3", str(agent)] if agent.suffix == ".py" else [str(agent)]
    proc = subprocess.run(cmd, input=json.dumps(case), capture_output=True, text=True,
                          timeout=timeout, cwd=agent.parent)
    if proc.returncode != 0:
        raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    try:
        return str(json.loads(proc.stdout).get("output"))
    except ValueError:
        return proc.stdout.strip()


def evaluate(folder: pathlib.Path, cfg: dict[str, Any], out: pathlib.Path) -> dict[str, Any]:
    metrics = {"correctness": True, "latency": True, "tokens": True, **(cfg.get("metrics") or {})}
    repeats = int(cfg.get("repeats") or 1)
    agent = folder / cfg["agent"] if cfg.get("agent") else None
    model = cfg.get("model") or {}
    timeout = int(cfg.get("timeout_s") or 300)

    deepeval, de_info = _load_deepeval() if metrics["correctness"] else (None, {"available": False, "note": "off in config"})
    otel, otel_info = _load_otel() if metrics["latency"] and agent else (None, {"available": False, "note": "off in config or no agent"})
    tracer, exporter = otel if otel else (None, None)

    proxy_url = None
    if metrics["tokens"] and agent and model.get("base_url"):
        proxy_url = proxy.start(model["base_url"], os.environ.get("EVAL_LLM_API_KEY", ""),
                                model.get("auth_header", "Authorization"), model.get("name", ""))
        os.environ["OPENAI_BASE_URL"] = f"{proxy_url}/v1"
        os.environ.setdefault("OPENAI_API_KEY", "counted-by-proxy")
        if model.get("name"):
            os.environ["EVAL_LLM_MODEL"] = model["name"]

    run_id = os.environ.get("REANA_WORKFLOW_UUID") or f"local-{datetime.datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    report: dict[str, Any] = {
        "utc": datetime.datetime.now(UTC).isoformat(timespec="seconds"), "run_id": run_id,
        "ran_on": "reana" if os.environ.get("REANA_WORKFLOW_UUID") else "local",
        "folder": str(folder), "agent": str(cfg.get("agent") or None), "repeats": repeats,
        "metrics": metrics, "deepeval": de_info, "opentelemetry": otel_info, "datasets": [],
    }
    lines = [f"evalwrap   run {run_id if report['ran_on'] == 'local' else run_id[:8]}   "
             f"on {report['ran_on']}   {repeats} repeat{'s' if repeats != 1 else ''}",
             "  " + "   ".join([
                 f"deepeval {de_info.get('version')}" if de_info.get("available") else "deepeval off",
                 "opentelemetry on" if otel_info.get("available") else "opentelemetry off",
                 f"proxy {proxy_url.rsplit(':', 1)[-1]}" if proxy_url else "proxy off"]), ""]

    rows = []
    for cases_path in sorted((folder / "dataset").rglob("cases.jsonl")):
        cases = [json.loads(l) for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        name = str(cases_path.parent.relative_to(folder / "dataset"))
        name = "dataset" if name == "." else name
        scored, latencies, errors = [], [], []
        for rep in range(repeats):
            for row in cases:
                want = str(row.get("expected_output") or "")
                got, prov, err = str(row.get("actual_output") or ""), "self_reported", None
                if agent:
                    started = time.perf_counter()
                    try:
                        got, prov = _invoke(agent, row, timeout), "measured"
                    except Exception as exc:  # noqa: BLE001
                        err = f"{type(exc).__name__}: {exc}"
                        errors.append(err)
                    ms = round((time.perf_counter() - started) * 1000, 1)
                    if metrics["latency"]:
                        latencies.append(ms)
                    if tracer:
                        with tracer.start_as_current_span("invoke_agent") as span:
                            span.set_attribute("evalwrap.case", str(row.get("name") or ""))
                            span.set_attribute("evalwrap.latency_ms", ms)
                if rep == 0:
                    scored.append({"name": row.get("name") or row.get("input", "")[:40], "expected": want,
                                   "actual": got, "provenance": prov, "error": err,
                                   **(_score(want, got, deepeval) if want and got and metrics["correctness"] else {})})
        graded = [s for s in scored if "normalised" in s]
        passed = [s for s in graded if s["normalised"]]
        gate = "not scored" if not graded else "pass" if len(passed) == len(graded) else "fail"
        latency = ({"mean_ms": round(statistics.mean(latencies), 1),
                    "sd_ms": round(statistics.stdev(latencies), 1) if len(latencies) > 1 else None,
                    "provenance": "measured"} if latencies else {"provenance": "not_available"})
        report["datasets"].append({"dataset": name, "cases": len(cases), "graded": len(graded),
                                   "passed": len(passed), "gate": gate, "latency": latency,
                                   "errors": errors[:5], "results": scored})
        lat = (f"{latency['mean_ms']:.0f}" + (f" ± {latency['sd_ms']:.0f}" if latency.get("sd_ms") else "") + " ms"
               if latencies else "not measured")
        rows.append((name, len(cases), "-" if not graded else str(len(passed)), gate, lat,
                     " ".join(errors[0].split("\n")[0].split())[:72] if errors else None, len(errors)))

    tokens = proxy.summary() if proxy_url else {"provenance": "not_available"}
    report["tokens"] = tokens
    if exporter:
        report["spans"] = len(exporter.get_finished_spans())

    if rows:
        w = max(20, min(52, max(len(r[0]) for r in rows)))
        lines.append(f"  {'DATASET':<{w}} {'CASES':>5} {'PASS':>5}  {'GATE':<10} LATENCY")
        for name, n, p, gate, lat, first_err, n_err in rows:
            lines.append(f"  {name:<{w}} {n:>5} {p:>5}  {gate:<10} {lat}")
            if first_err:
                lines.append(f"       first error: {first_err}" + (f"  (+{n_err - 1} more, see report.json)" if n_err > 1 else ""))
        lines.append("")
    else:
        lines.append(f"  no dataset/cases.jsonl under {folder}")
    tok = tokens.get("total_tokens")
    if tok is not None:
        lines.append(f"  tokens   {tok:,} over {tokens['calls']} model call{'s' if tokens['calls'] != 1 else ''} ({tokens['provenance']})")
    elif not proxy_url:
        lines.append("  tokens   not measured (" + ("off in config" if not metrics["tokens"] else
                     "no agent" if not agent else "model.base_url not set in config") + ")")
    elif tokens.get("calls"):
        lines.append(f"  tokens   not measured, {tokens['calls']} model calls made, none returned a usage block")
        if tokens.get("unparsed_example"):
            lines.append(f"           upstream replied: {tokens['unparsed_example'][:80]}")
    else:
        lines.append("  tokens   not measured, no model calls were made")
    tally: dict[str, int] = {}
    for d in report["datasets"]:
        tally[d["gate"]] = tally.get(d["gate"], 0) + 1
    lines.append("  gate     " + (" · ".join(f"{v} {k}" for k, v in tally.items()) or "nothing found"))

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines) + f"\n\n  {out / 'report.json'}\n")
    return report
