"""Exercise the live MCP server, score the answers, and measure the calls.

Walks the layout the project already uses, which is the layout the data has
in dCache:

    <suite>/datasets/<use_case_id>/<dataset_id>/cases.jsonl
    <suite>/use_cases/<use_case_id>/system.json
    <suite>/results/experiments/<use_case_id>/<run_id>/     <- written here

`cases.jsonl` is one JSON object per line using DeepEval's own `Golden`
field names, so a row loads with `Golden(**row)` and there is no
translation layer to drift. What is new here sits in `additional_metadata`:
which MCP tool answers the case, with which arguments, and which part of
the response is the answer.

THE THREE-VALUED GATE, kept from eval_benchmark.py on branch
`deepeval-a-data`. A dataset comes out `pass`, `fail` or `not-scored`. A
case with no `actual_output` was never answered, so it is not scored, and
it is counted as neither a pass nor a failure. Without a token every case
is in that state, and the card still writes a report.

Those two states are kept apart carefully:

* The call never happened, or the transport failed: `actual_output` stays
  empty and the case is not scored.
* The call succeeded and the answer lacked the field the case asks about:
  `actual_output` becomes the JSON literal `null` and the case is scored,
  and fails. The server answered; the answer was wrong.

DeepEval supplies the comparison when it is installed, using only metrics
that consult no model: ExactMatchMetric, PatternMatchMetric and
JsonCorrectnessMetric. This card has no model endpoint, so a judged metric
has nothing to run against. When the install failed, the same comparison is
computed in plain Python and the report says which path produced it. A
verdict is produced either way.

Exits 0 when the token is absent, when the server is unreachable, when it
refuses the credential, and when either install failed. A missing credential
is not a broken card. An unexpected failure is still allowed to fail the
step, because a card that swallows those tells nobody anything.
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

from mcp_client import DOCUMENTED_TOOLS, MCPClient, Telemetry, new_run_id

DEFAULT_BASE = "https://physicsllm-rdm.desy.de:8443"


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


class Scorer:
    """The three no-judge metrics, with a plain-Python twin for each.

    DeepEval is the preferred path because a metric everyone else already
    runs is a result everyone else can read. The twin exists so that a
    failed install downgrades the provenance of the number rather than
    removing the number.

    `JsonCorrectnessMetric` needs a model object at construction even though
    its scoring is `pydantic.model_validate_json` and consults nothing. It
    is given one that raises if it is ever called, together with
    `include_reason=False`, which is the only path on which the model would
    have been reached. Verified against deepeval 4.2.3.
    """

    def __init__(self) -> None:
        self.available = False
        self.error: str | None = None
        try:
            from deepeval.metrics import (
                ExactMatchMetric,
                JsonCorrectnessMetric,
                PatternMatchMetric,
            )
            from deepeval.test_case import LLMTestCase
        except Exception as exc:  # noqa: BLE001 - absence is a reported state
            self.error = f"{type(exc).__name__}: {exc}"
            return
        self._exact = ExactMatchMetric
        self._pattern = PatternMatchMetric
        self._json = JsonCorrectnessMetric
        self._case = LLMTestCase
        self._no_judge = _no_judge_factory()
        self.available = True

    # -- the metrics -------------------------------------------------------

    def exact_match(self, actual: str, expected: str) -> tuple[bool, str, str]:
        if not self.available:
            return (
                actual.strip() == expected.strip(),
                "ExactMatch computed without DeepEval",
                "fallback",
            )
        metric = self._exact()
        case = self._case(input="mcp", actual_output=actual, expected_output=expected)
        metric.measure(case, _show_indicator=False)
        return metric.is_successful(), str(metric.reason), "deepeval:ExactMatchMetric"

    def pattern_match(self, actual: str, pattern: str) -> tuple[bool, str, str]:
        if not self.available:
            ok = re.fullmatch(pattern, actual.strip()) is not None
            return ok, "PatternMatch computed without DeepEval", "fallback"
        metric = self._pattern(pattern=pattern)
        case = self._case(input="mcp", actual_output=actual)
        metric.measure(case, _show_indicator=False)
        return metric.is_successful(), str(metric.reason), "deepeval:PatternMatchMetric"

    def json_correctness(
        self, actual: str, required: list[str], optional: list[str]
    ) -> tuple[bool, str, str]:
        # A pydantic model's fields have to be Python identifiers, so a case
        # naming a JSON key such as `$schema` cannot be expressed as one.
        # That case is compared directly instead, and the report records
        # which path produced the number.
        expressible = all(name.isidentifier() for name in [*required, *optional])
        if not self.available or not expressible:
            try:
                parsed = json.loads(actual)
            except json.JSONDecodeError as exc:
                return False, f"not JSON: {exc}", "fallback"
            if not isinstance(parsed, dict):
                return False, f"JSON is a {type(parsed).__name__}, not an object", "fallback"
            missing = [name for name in required if name not in parsed]
            return (
                not missing,
                "all required fields present" if not missing else f"missing: {missing}",
                "fallback" if self.available else "fallback (deepeval absent)",
            )
        model = _schema_model(required, optional)
        metric = self._json(
            expected_schema=model,
            model=self._no_judge(),
            include_reason=False,
            async_mode=False,
        )
        case = self._case(input="mcp", actual_output=actual)
        metric.measure(case, _show_indicator=False)
        ok = metric.is_successful()
        return (
            ok,
            "validates against the expected field set"
            if ok
            else f"does not validate against required {required}",
            "deepeval:JsonCorrectnessMetric",
        )


def _schema_model(required: list[str], optional: list[str]):
    """A pydantic model with the field names the case declares.

    Every field is typed `Any`, because the case states which fields the
    response must carry and says nothing about their types. Claiming a type
    the case never asserted would make the metric fail for a reason nobody
    wrote down.
    """
    from pydantic import create_model

    fields: dict[str, Any] = {name: (Any, ...) for name in required}
    fields.update({name: (Any, None) for name in optional})
    return create_model("ExpectedResponse", **fields)


def _no_judge_factory():
    """A model object that satisfies construction and refuses to be used.

    `JsonCorrectnessMetric.__init__` calls `initialize_model`, which
    defaults to OpenAI and raises without an API key, and which rejects any
    object that is not a `DeepEvalBaseLLM`. This card has no model endpoint
    and needs none: the metric scores with `pydantic.model_validate_json`,
    and with `include_reason=False` the only path that would reach a model
    is dead. If that ever changes, this raises loudly rather than quietly
    reaching for a judge.

    Built here rather than at import time because `DeepEvalBaseLLM` only
    exists when deepeval does.
    """
    from deepeval.models import DeepEvalBaseLLM

    refusal = "this card has no model endpoint and scores nothing with a judge"

    class NoJudge(DeepEvalBaseLLM):
        def __init__(self) -> None:
            return None

        def load_model(self) -> None:
            return None

        def generate(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError(refusal)

        async def a_generate(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError(refusal)

        def get_model_name(self) -> str:
            return "none (schema validation consults no model)"

    return NoJudge


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


def discover(root: pathlib.Path):
    """Every cases.jsonl under `root`, with the identifiers its path encodes.

    Two shapes are accepted, because the path a run is given depends on what
    is mounted. A tree holding several suites is the local mirror's shape; a
    single suite directory is what an S4P mount of one suite looks like.
    Both are recognised by where `datasets/` sits, so neither needs a flag.

    `suite_root` is the directory that owns `datasets/`, `use_cases/` and
    `results/`, and everything else is resolved relative to it.
    """
    seen: set[pathlib.Path] = set()
    for pattern in ("*/datasets/*/*/cases.jsonl", "datasets/*/*/cases.jsonl"):
        for path in sorted(root.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            dataset_dir = path.parent
            suite_root = dataset_dir.parents[2]
            yield {
                "suite": suite_root.name,
                "suite_root": suite_root,
                "use_case_id": dataset_dir.parent.name,
                "dataset_id": dataset_dir.name,
                "cases": path,
            }


def system_under_test(suite_root: pathlib.Path, use_case: str) -> dict:
    """What produces the answer, and therefore which metrics can apply."""
    path = suite_root / "use_cases" / use_case / "system.json"
    if not path.is_file():
        return {
            "kind": "unknown",
            "declared": False,
            "note": "no system.json; nothing says what answers these cases",
        }
    config = json.loads(path.read_text())
    config["declared"] = True
    config["path"] = str(path)
    return config


_MISSING = object()


def extract(value: Any, path: str) -> Any:
    """Follow a dotted path into a tool's return value."""
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
            else:
                return _MISSING
        else:
            return _MISSING
    return current


def render(value: Any) -> str:
    """A tool's answer as the string a metric compares.

    A string is passed through so a golden reads as the value itself.
    Anything else is JSON with sorted keys, so two runs that returned the
    same object produce the same bytes.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------
# Answering one case
# --------------------------------------------------------------------------


def answer(client: MCPClient | None, row: dict) -> dict:
    """Ask the server the case's question and report what came back.

    Returns the fields this runner adds to the row: `actual_output`, and a
    record of the call that produced it.
    """
    meta = row.get("additional_metadata") or {}
    tool = meta.get("tool")
    out: dict[str, Any] = {"actual_output": "", "call": None}

    if client is None:
        out["not_run"] = "no MCP session (no token, or the handshake failed)"
        return out
    if not tool:
        out["not_run"] = "the case names no tool in additional_metadata.tool"
        return out
    if tool not in DOCUMENTED_TOOLS:
        out["not_run"] = f"{tool!r} is not one of the nine documented tools"
        return out

    record = client.call_tool(tool, meta.get("arguments") or {})
    out["call"] = {
        "tool": tool,
        "ok": record.ok,
        "latency_ms": record.get("latency_ms"),
        "response_bytes": record.get("response_bytes"),
        "http_status": record.get("http_status"),
        "error": record.get("error"),
        "tool_error": bool(record.get("tool_error")),
    }

    # A case that scores whether the call itself was refused. The server
    # refusing a schema id it cannot back is the behaviour under test, so
    # the outcome word is the answer.
    if meta.get("observe") == "call_outcome":
        out["actual_output"] = "error" if not record.ok else "ok"
        return out

    if not record.ok:
        # Nothing was answered. The error is recorded above; the case stays
        # unscored rather than being counted as a wrong answer.
        out["not_run"] = f"call failed: {record.get('error')}"
        return out

    found = extract(record.get("value"), meta.get("extract") or "")
    if found is _MISSING:
        # The server answered and the answer lacked the field. That is a
        # real difference, so it is scored and it fails.
        out["actual_output"] = "null"
        out["extract_missing"] = meta.get("extract")
        return out

    out["actual_output"] = render(found)
    return out


def score(scorer: Scorer, row: dict, actual: str) -> dict:
    """One case, scored by the metric it declares.

    A metric that raises is reported as a metric that raised. A card that
    dies partway through tells nobody anything about the cases it had
    already answered.
    """
    try:
        return _score(scorer, row, actual)
    except Exception as exc:  # noqa: BLE001
        return {
            "metric": (row.get("additional_metadata") or {}).get("metric") or "exact_match",
            "scored": False,
            "pass": None,
            "reason": f"the metric raised: {type(exc).__name__}: {exc}",
            "computed_by": "none",
        }


def _score(scorer: Scorer, row: dict, actual: str) -> dict:
    meta = row.get("additional_metadata") or {}
    metric = meta.get("metric") or "exact_match"
    expected = row.get("expected_output") or ""

    if metric == "reference_free":
        return {
            "metric": "reference_free",
            "scored": False,
            "pass": None,
            "reason": meta.get("reference_free_reason")
            or "no expected value can be stated for this case, so nothing is gated on it",
            "computed_by": "none",
        }
    if metric == "pattern_match":
        pattern = meta.get("pattern") or ""
        ok, reason, by = scorer.pattern_match(actual, pattern)
        return {"metric": "pattern_match", "pattern": pattern, "scored": True,
                "pass": ok, "reason": reason, "computed_by": by}
    if metric == "json_correctness":
        required = list(meta.get("json_required") or [])
        optional = list(meta.get("json_optional") or [])
        ok, reason, by = scorer.json_correctness(actual, required, optional)
        return {"metric": "json_correctness", "required": required, "optional": optional,
                "scored": True, "pass": ok, "reason": reason, "computed_by": by}

    ok, reason, by = scorer.exact_match(actual, expected)
    return {"metric": "exact_match", "scored": True, "pass": ok,
            "reason": reason, "computed_by": by}


# --------------------------------------------------------------------------
# The tool surface
# --------------------------------------------------------------------------


def surface(client: MCPClient | None) -> dict:
    """What the live server advertises, against what it is documented to.

    Worth its own section: an existing `system.json` in this repository
    names four tools this endpoint has never exposed. Asking the server is
    how that stops being a matter of opinion.
    """
    report: dict[str, Any] = {
        "documented": list(DOCUMENTED_TOOLS),
        "documented_count": len(DOCUMENTED_TOOLS),
        "source": "services/mcp/src/rdm_mcp/server.py in the physics-llm-rdm repository",
    }
    if client is None:
        report["checked"] = False
        report["verdict"] = "not checked: no MCP session"
        return report

    tools, record = client.list_tools()
    report["checked"] = record.ok
    if not record.ok:
        report["verdict"] = f"tools/list failed: {record.get('error')}"
        return report

    advertised = sorted(str(tool.get("name") or "") for tool in tools)
    report["advertised"] = advertised
    report["advertised_count"] = len(advertised)
    report["missing"] = sorted(set(DOCUMENTED_TOOLS) - set(advertised))
    report["extra"] = sorted(set(advertised) - set(DOCUMENTED_TOOLS))
    # Every tool on this server is annotated read_only_hint=true. A tool
    # that is not is a change worth seeing in a report rather than in a
    # postmortem.
    report["not_read_only"] = sorted(
        str(tool.get("name") or "")
        for tool in tools
        if not ((tool.get("annotations") or {}).get("readOnlyHint"))
    )
    report["verdict"] = (
        "the live surface matches the nine documented read-only tools"
        if not report["missing"] and not report["extra"] and not report["not_read_only"]
        else f"missing={report['missing']} extra={report['extra']} "
        f"not_read_only={report['not_read_only']}"
    )
    return report


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", default="benchmark",
                        help="the benchmark root holding <suite>/datasets/...")
    parser.add_argument("--base", default=os.environ.get("MCP_BASE", DEFAULT_BASE))
    parser.add_argument("--tls", choices=("verify", "insecure"), default="insecure")
    parser.add_argument("--out", default="results")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--write-results", action="store_true",
                        help="also write into <suite>/results/experiments/...")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = pathlib.Path(args.tree)
    run_id = args.run_id or new_run_id()
    token = (os.environ.get("RDM_MCP_BEARER_TOKEN") or "").strip()

    telemetry = Telemetry()
    scorer = Scorer()

    report: dict[str, Any] = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_id": run_id,
        "tree": str(root),
        "mcp": {
            "base": args.base,
            "endpoint": args.base.rstrip("/") + "/mcp",
            "tls_mode": args.tls,
            "token_supplied": bool(token),
        },
        "deepeval": {"available": scorer.available, "error": scorer.error},
        "opentelemetry": {"available": telemetry.enabled, "error": telemetry.error},
        "datasets": [],
    }

    # `measured` is whichever client object was built, and it keeps the call
    # records even when the handshake failed. `client` is set only once
    # there is a usable session, so a failed handshake still shows up in the
    # latency summary while no case is asked against a dead connection.
    measured: MCPClient | None = None
    client: MCPClient | None = None
    if not token:
        report["mcp"]["session"] = (
            "no token: set RDM_MCP_BEARER_TOKEN in the REANA secret store with "
            "reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<value>"
        )
    else:
        measured = MCPClient(
            args.base, token, tls_mode=args.tls, timeout=args.timeout, telemetry=telemetry
        )
        handshake = measured.initialize()
        if handshake.ok:
            client = measured
            report["mcp"]["session"] = "established"
            report["mcp"]["protocol_version"] = measured.protocol_version
            report["mcp"]["server_info"] = measured.server_info
        else:
            report["mcp"]["session"] = f"handshake failed: {handshake.get('error')}"
            report["mcp"]["handshake_http_status"] = handshake.get("http_status")

    report["tool_surface"] = surface(client)

    entries = list(discover(root))
    if not entries:
        report["verdict"] = (
            f"no cases.jsonl found under {root}: expected "
            f"<suite>/datasets/<use_case>/<dataset>/cases.jsonl, or "
            f"datasets/<use_case>/<dataset>/cases.jsonl when {root} is one suite"
        )
    for entry in entries:
        rows = [json.loads(line) for line in entry["cases"].read_text().splitlines() if line.strip()]
        sut = system_under_test(entry["suite_root"], entry["use_case_id"])

        cases_out: list[dict[str, Any]] = []
        for row in rows:
            produced = answer(client, row)
            actual = produced["actual_output"]
            case: dict[str, Any] = {
                "name": row.get("name"),
                "tool": (row.get("additional_metadata") or {}).get("tool"),
                "arguments": (row.get("additional_metadata") or {}).get("arguments"),
                "extract": (row.get("additional_metadata") or {}).get("extract"),
                "expected_output": row.get("expected_output") or "",
                "actual_output": actual,
                "call": produced.get("call"),
            }
            if produced.get("extract_missing") is not None:
                case["extract_missing"] = produced["extract_missing"]
            if not actual.strip():
                # Never answered. Not a pass and not a failure.
                case["scored"] = False
                case["pass"] = None
                case["reason"] = produced.get("not_run") or "no actual_output"
                cases_out.append(case)
                continue
            case.update(score(scorer, row, actual))
            cases_out.append(case)

        scored = [c for c in cases_out if c.get("scored")]
        failed = [c for c in scored if not c.get("pass")]
        result = {
            "suite": entry["suite"],
            "use_case_id": entry["use_case_id"],
            "dataset_id": entry["dataset_id"],
            "system_under_test": {
                "kind": sut.get("kind"),
                "declared": sut.get("declared"),
                "mcp_base": (sut.get("mcp_server") or {}).get("base"),
            },
            "cases": len(rows),
            "cases_scored": len(scored),
            "cases_not_scored": len(cases_out) - len(scored),
            "failures": [c["name"] for c in failed],
            # Three outcomes. Nothing scored is not a pass, and it is not a
            # failure of the system either.
            "gate": ("not-scored" if not scored else "pass" if not failed else "fail"),
            "fields": cases_out,
        }
        if not scored:
            result["not_scored"] = (
                f"{len(cases_out)} case(s) produced no actual_output. "
                f"The system under test is {sut.get('kind')}, reached over MCP, "
                "which needs RDM_MCP_BEARER_TOKEN and a reachable server."
            )
        report["datasets"].append(result)

        if args.write_results:
            _write_experiment(entry, run_id, result, report, telemetry)

    otel = measured.summary() if measured else _empty_summary(telemetry)
    report["otel_summary"] = otel
    spans = telemetry.finished_spans()

    tally = collections.Counter(d["gate"] for d in report["datasets"])
    report.setdefault(
        "verdict",
        f"{len(entries)} dataset(s); {tally['pass']} passed the gate, "
        f"{tally['fail']} failed, {tally['not-scored']} not scored",
    )

    (out / "mcp_card_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "otel_summary.json").write_text(json.dumps(otel, indent=2) + "\n")
    (out / "otel_spans.json").write_text(json.dumps(spans, indent=2) + "\n")
    _print(report, otel, spans)
    return 0


def _empty_summary(telemetry: Telemetry) -> dict:
    return {
        "semconv_version": "1.38.0",
        "opentelemetry": {"available": telemetry.enabled, "error": telemetry.error},
        "total_calls": 0,
        "total_wall_ms": 0.0,
        "per_tool": {},
        "note": "no MCP client was built, so no call was measured",
    }


def _write_experiment(entry, run_id, result, report, telemetry) -> None:
    """Per-run outputs, in the places the real tree already has for them."""
    base = entry["suite_root"] / "results" / "experiments" / entry["use_case_id"] / run_id
    for folder in ("metrics", "summary", "traces"):
        (base / folder).mkdir(parents=True, exist_ok=True)
    (base / "metrics" / f"{entry['dataset_id']}.deepeval.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    (base / "traces" / "otel_spans.json").write_text(
        json.dumps(telemetry.finished_spans(), indent=2) + "\n"
    )
    (base / "config_run.json").write_text(
        json.dumps(
            {
                "suite": entry["suite"],
                "use_case_id": entry["use_case_id"],
                "dataset_id": entry["dataset_id"],
                "run_id": run_id,
                "mcp": report["mcp"],
                "metrics": ["exact_match", "pattern_match", "json_correctness"],
                "deepeval": report["deepeval"],
                "opentelemetry": report["opentelemetry"],
            },
            indent=2,
        )
        + "\n"
    )


def _print(report: dict, otel: dict, spans: list) -> None:
    mcp = report["mcp"]
    print(f"MCP card   run {report['run_id']}   {report['utc']}")
    print(f"endpoint:  {mcp['endpoint']}  tls={mcp['tls_mode']}")
    print(f"session:   {mcp.get('session')}")
    print(
        "deepeval:  "
        + ("present" if report["deepeval"]["available"] else f"absent ({report['deepeval']['error']})")
    )
    print(
        "otel:      "
        + (
            f"present, {len(spans)} span(s)"
            if report["opentelemetry"]["available"]
            else f"absent ({report['opentelemetry']['error']})"
        )
    )

    tools = report["tool_surface"]
    print(f"\ntool surface: {tools['verdict']}")
    if tools.get("advertised"):
        print(f"  advertised ({tools['advertised_count']}): {', '.join(tools['advertised'])}")

    for dataset in report["datasets"]:
        print(
            f"\n  {dataset['suite']}/{dataset['use_case_id']}/{dataset['dataset_id']}  "
            f"{dataset['cases_scored']}/{dataset['cases']} scored  "
            f"gate={dataset['gate'].upper()}"
        )
        if dataset.get("not_scored"):
            print(f"      NOT SCORED: {dataset['not_scored']}")
        for case in dataset["fields"]:
            if case.get("scored"):
                mark = "ok  " if case.get("pass") else "FAIL"
            else:
                mark = "--  "
            latency = (case.get("call") or {}).get("latency_ms")
            timing = f"{latency:>8.1f}ms" if isinstance(latency, (int, float)) else " " * 10
            print(f"      {mark} {str(case['name'])[:46]:<48}{timing}  {str(case.get('reason'))[:50]}")

    print(f"\nOpenTelemetry summary  ({otel['total_calls']} calls, "
          f"{otel['total_wall_ms']:.1f} ms total wall time)")
    for name, stats in (otel.get("per_tool") or {}).items():
        mean = stats["mean_latency_ms"]
        stdev = stats["stdev_latency_ms"]
        print(
            f"  {name:<28} n={stats['calls']:<3} ok={stats['ok']:<3} "
            f"mean={mean if mean is None else f'{mean:.1f}'}ms "
            f"sd={'n/a' if stdev is None else f'{stdev:.1f}ms'} "
            f"bytes={stats['total_response_bytes']}"
        )

    print(f"\n{report['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
