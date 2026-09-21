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
    # token_env used to be whatever the bundle asked for, so a fetched
    # system.json could name DCACHE_BEARER_TOKEN and have it posted to a URL
    # of its choosing. Credential exfiltration with no code execution needed.
    # One fixed name now, and it is not one of ours.
    token = os.environ.get("SYSTEM_UNDER_TEST_TOKEN", "")
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


def _resolve_in_dataset(arg: str, folder: pathlib.Path | None) -> str:
    """A relative script path in system.json means relative to the dataset.

    Anything else surprises the person who wrote it: they put agent.py
    beside their cases and reasonably expect it to be found, whatever
    directory the harness happens to be run from.
    """
    if folder is None or pathlib.Path(arg).is_absolute():
        return arg
    if pathlib.Path(arg).exists():
        return arg
    candidate = folder / pathlib.Path(arg).name
    if candidate.exists():
        return str(candidate)
    candidate = folder / arg
    return str(candidate) if candidate.exists() else arg


def _invoke_subprocess(spec: dict[str, Any], case: dict[str, Any]) -> str:
    cmd = spec.get("command")
    if not cmd:
        raise RuntimeError("system.json kind=subprocess needs 'command'")
    folder = spec.get("_dataset_dir")
    folder = pathlib.Path(folder) if folder else None
    if isinstance(cmd, list):
        cmd = [cmd[0]] + [_resolve_in_dataset(str(x), folder) for x in cmd[1:]]
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

# Running a system means doing what its system.json says: executing a command,
# importing a module, or posting to a URL. That is correct for a bundle you
# wrote and dangerous for one you fetched, because the job holds storage and
# model credentials. So it is off unless asked for.
EXEC_WARNING = (
    "system.json declares kind={kind!r}, which would {action}. This runs only "
    "with --allow-exec, because a fetched bundle can name any command, module "
    "or URL, and this job holds credentials. Scored without it."
)
EXEC_ACTIONS = {
    "subprocess": "execute a command from the bundle",
    "python_entrypoint": "import and call code named by the bundle",
    "http_endpoint": "post case content to a URL named by the bundle",
}


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
    ap.add_argument("--allow-exec", action="store_true",
                    help="run the system each system.json declares. Off by "
                         "default: a fetched bundle can name any command, "
                         "module or URL, and this process holds credentials.")
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
    def _short(info: dict, name: str, ok: str) -> str:
        return f"{name} {ok}" if info["available"] else f"{name} absent"

    rid = str(report["run_id"])
    # A REANA uuid is unique in its first 8; a local id is not, and
    # cutting it there turns "local-20260921T182952Z" into "local-20".
    run_short = rid[:8] if report["ran_on"] == "reana" else rid
    print(f"\neval harness   run {run_short}   on {report['ran_on']}   "
          f"{a.repeats} repeat{'s' if a.repeats != 1 else ''}")
    print("  " + "   ".join([
        _short(de_info, "deepeval", de_info.get("version", "?")),
        _short(otel_info, "opentelemetry", otel_info.get("semconv", "ok")),
        f"proxy {proxy_url.rsplit('/', 1)[-1]}" if proxy_url else "proxy off",
    ]))
    print()

    rows: list[dict[str, Any]] = []   # one display row per dataset
    found = sorted(root.rglob("cases.jsonl"))
    if not found:
        report["verdict"] = f"no cases.jsonl anywhere under {root}"
        print(report["verdict"])
    for cases_path in found:
        rows_in = [json.loads(l) for l in
                   cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        system = _find_system(cases_path, root)
        # So a relative script path in system.json resolves against the folder
        # the author put it in, not against wherever the harness was started.
        if system:
            system["_dataset_dir"] = str(cases_path.parent)
        kind = str(system.get("kind") or "")
        rel_name = str(cases_path.parent.relative_to(root))
        # relative_to returns "." when --tree points straight at the dataset;
        # the folder name is what the reader expects to see there.
        name = cases_path.parent.name if rel_name in (".", "") else rel_name

        invoker = INVOKERS.get(kind) if a.allow_exec else None
        refused = (EXEC_WARNING.format(kind=kind, action=EXEC_ACTIONS[kind])
                   if kind in INVOKERS and not a.allow_exec else None)
        if refused:
            unhandled = refused
        elif kind and not invoker and kind != "static_artifact":
            # A kind we do not implement is not a missing credential and not a
            # broken dataset. Saying so is the difference between a report
            # someone can act on and one that sends them hunting a token they
            # already have. static_artifact is handled: the answers are in the
            # file, which is what scoring them as they arrive means.
            unhandled = (f"system.json declares kind={kind!r}, which this "
                         f"harness does not invoke; handled kinds are "
                         f"{', '.join(sorted(INVOKERS))}, or static_artifact "
                         f"for answers already in the file")
        else:
            unhandled = None
        scored, latencies, errors = [], [], []
        for rep in range(a.repeats):
            for row in rows_in:
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
            "dataset": name, "cases": len(rows_in), "kind": kind or "unspecified",
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
        rows.append({
            "name": name, "cases": len(rows_in), "passed": len(passed),
            "graded": len(graded), "gate": gate,
            "latency": (f"{entry['latency_ms']['mean']:.0f}"
                        + (f" \u00b1 {entry['latency_ms']['sd']:.0f}"
                           if entry["latency_ms"].get("sd") else "")
                        + " ms") if entry["latency_ms"].get("mean") else "not measured",
            "note": unhandled,
            # One line, never a pasted traceback: the full text is in the JSON.
            # One line, never a pasted traceback. The first line of an
            # exception says what it was; the rest is in the JSON.
            "error": (" ".join(errors[0].split("\n")[0].split())[:72]
                      if errors else None),
            "error_count": len(errors),
        })

    tokens = counting_proxy.summary() if proxy_url else {
        "provenance": "not_available", "note": proxy_note}
    report["tokens"] = tokens
    if otel and exporter:
        report["spans"] = len(exporter.get_finished_spans())

    # ---- the table -------------------------------------------------------
    if rows:
        w = max(38, min(52, max(len(r["name"]) for r in rows)))
        print(f"  {'DATASET':<{w}} {'CASES':>5} {'PASS':>5}  {'GATE':<10} LATENCY")
        for r in rows:
            passed = "-" if r["gate"] == "NOT-SCORED" else str(r["passed"])
            gate = {"pass": "pass", "fail": "fail"}.get(r["gate"], "not scored")
            name = r["name"] if len(r["name"]) <= w else "..." + r["name"][-(w - 3):]
            print(f"  {name:<{w}} {r['cases']:>5} {passed:>5}  {gate:<10} {r['latency']}")
            if r["note"]:
                print(f"       {r['note']}")
            if r["error"]:
                more = (f"  (+{r['error_count'] - 1} more, see the report)"
                        if r["error_count"] > 1 else "")
                print(f"       first error: {r['error']}{more}")
        print()

    # ---- the two lines people actually read ------------------------------
    tally: dict[str, int] = {}
    for d in report["datasets"]:
        tally[d["gate"]] = tally.get(d["gate"], 0) + 1
    order = {"pass": 0, "fail": 1, "NOT-SCORED": 2}
    gates = " \u00b7 ".join(
        f"{v} {'not scored' if k == 'NOT-SCORED' else k}"
        for k, v in sorted(tally.items(), key=lambda kv: order.get(kv[0], 9)))

    tok = tokens.get("total_tokens")
    calls_made = tokens.get("calls", 0)
    if tok is None:
        if not proxy_url:
            why = "no counting proxy: EVAL_LLM_BASE_URL is unset"
        elif calls_made:
            why = (f"{calls_made} model call{'s' if calls_made != 1 else ''} made, "
                   "none returned a usage block")
        else:
            why = "no model calls were made"
        token_line = f"not measured, {why}"
    else:
        token_line = (f"{tok:,} over {tokens.get('calls', 0)} model call"
                      f"{'s' if tokens.get('calls', 0) != 1 else ''} "
                      f"({tokens.get('provenance')})")
    print(f"  tokens   {token_line}")
    if tokens.get("unparsed_example"):
        print(f"           upstream returned no usage block; body began: "
              f"{tokens['unparsed_example'][:80]}")
    print(f"  gate     {gates or 'nothing found'}")

    (out / "eval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  {out / 'eval_report.json'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
