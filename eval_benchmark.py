"""Score a whole benchmark tree with DeepEval, offline.

Walks the layout the project already uses and scores every dataset that
ships cases:

    <suite>/datasets/<use_case_id>/<dataset_id>/cases.jsonl
    <suite>/results/experiments/<use_case_id>/<run_id>/     <- written here

`cases.jsonl` is one JSON object per line using DeepEval's own `Golden`
field names - input, expected_output, actual_output, context,
additional_metadata, name, source_file - so the file loads with
`Golden(**row)` and no translation layer. That is the whole reason to
prefer those names over invented ones: the dataset is portable to anyone
who already uses DeepEval, and there is no mapping code to drift.

Needs no dCache and no MCP server. The tree can be a local mirror; the
paths are the same either way.

Always exits 0.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys

FALLBACK_FIELDS = ("exact", "normalised")


def normalise(field: str, value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if "time" in field.lower() or "date" in field.lower():
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone(datetime.timezone.utc).isoformat()
        except ValueError:
            pass
    return text


def load_deepeval():
    try:
        from deepeval.dataset import Golden
        from deepeval.metrics import BaseMetric
        from deepeval.test_case import LLMTestCase
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    class DeterministicFieldMatch(BaseMetric):
        """A real DeepEval metric that consults no model."""

        def __init__(self, field: str, strict: bool):
            self.threshold, self.field, self.strict = 1.0, field, strict
            self.evaluation_model, self.async_mode = "deterministic", False
            self.strict_mode, self.verbose_mode = strict, False

        def measure(self, test_case) -> float:
            got, want = test_case.actual_output, test_case.expected_output
            if not self.strict:
                got, want = normalise(self.field, got), normalise(self.field, want)
            self.score = 1.0 if got == want else 0.0
            self.success = self.score >= self.threshold
            self.reason = "identical" if self.success else f"expected {want!r}, got {got!r}"
            return self.score

        async def a_measure(self, test_case, *_a, **_k) -> float:
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return bool(getattr(self, "success", False))

        @property
        def __name__(self):  # noqa: A003
            return f"{'exact' if self.strict else 'normalised'}:{self.field}"

    return (Golden, LLMTestCase, DeterministicFieldMatch), None


def discover(root: pathlib.Path):
    """Every cases.jsonl in the tree, with the identifiers its path encodes."""
    for path in sorted(root.glob("*/datasets/*/*/cases.jsonl")):
        dataset_dir = path.parent
        yield {
            "suite": dataset_dir.parents[2].name,
            "use_case_id": dataset_dir.parent.name,
            "dataset_id": dataset_dir.name,
            "cases": path,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="benchmark",
                    help="the benchmark root holding <suite>/datasets/...")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--write-results", action="store_true",
                    help="also write into <suite>/results/experiments/...")
    a = ap.parse_args()

    root = pathlib.Path(a.tree)
    run_id = (a.run_id or os.environ.get("REANA_WORKFLOW_UUID")
              or datetime.datetime.now(datetime.timezone.utc).strftime("local-%Y%m%dT%H%M%SZ"))

    bits, import_error = load_deepeval()
    judge_model = (os.environ.get("DEEPEVAL_JUDGE_MODEL")
                   or os.environ.get("LOCAL_LLM_MODEL") or "").strip()

    report: dict = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tree": str(root),
        "run_id": run_id,
        "deepeval": {"available": bits is not None, "error": import_error},
        "judge_model": judge_model or None,
        "datasets": [],
    }

    if not root.is_dir():
        report["verdict"] = f"no benchmark tree at {root}"
        finish(report)
        return 0

    found = list(discover(root))
    if not found:
        report["verdict"] = f"no cases.jsonl found under {root}/*/datasets/*/*/"
        finish(report)
        return 0

    for entry in found:
        rows = [json.loads(l) for l in entry["cases"].read_text().splitlines() if l.strip()]
        scored, exact_fail, norm_fail = [], [], []

        for row in rows:
            field = (row.get("additional_metadata") or {}).get("field") or row.get("name") or "value"
            want, got = row.get("expected_output") or "", row.get("actual_output") or ""

            if bits is not None:
                Golden, LLMTestCase, Det = bits
                Golden(**row)                      # validates the row is a real Golden
                case = LLMTestCase(input=row.get("input") or "",
                                   actual_output=got, expected_output=want)
                strict, loose = Det(field, True), Det(field, False)
                strict.measure(case); loose.measure(case)
                ok_exact, ok_norm = strict.is_successful(), loose.is_successful()
                reason = loose.reason
            else:
                ok_exact = got == want
                ok_norm = normalise(field, got) == normalise(field, want)
                reason = "computed without DeepEval"

            if not ok_exact:
                exact_fail.append(field)
            if not ok_norm:
                norm_fail.append(field)
            scored.append({"field": field, "exact": ok_exact,
                           "normalised": ok_norm, "reason": reason})

        result = {
            "suite": entry["suite"], "use_case_id": entry["use_case_id"],
            "dataset_id": entry["dataset_id"], "cases": len(rows),
            "exact_failures": exact_fail,
            "absorbed_by_normalisation": [f for f in exact_fail if f not in norm_fail],
            "real_differences": norm_fail,
            "gate": "pass" if not norm_fail else "fail",
            "fields": scored,
        }
        report["datasets"].append(result)

        if a.write_results:
            out = (root / entry["suite"] / "results" / "experiments"
                   / entry["use_case_id"] / run_id)
            (out / "metrics").mkdir(parents=True, exist_ok=True)
            (out / "metrics" / f"{entry['dataset_id']}.deepeval.json").write_text(
                json.dumps(result, indent=2) + "\n")
            (out / "config_run.json").write_text(json.dumps({
                "use_case_id": entry["use_case_id"], "run_id": run_id,
                "dataset_id": entry["dataset_id"], "suite": entry["suite"],
                "metrics": ["exact", "normalised"] + (["judged"] if judge_model else []),
                "deepeval": report["deepeval"],
            }, indent=2) + "\n")

    report["verdict"] = (
        f"{len(found)} dataset(s); "
        f"{sum(1 for d in report['datasets'] if d['gate'] == 'pass')} passed the gate")
    finish(report)
    return 0


def finish(report: dict) -> None:
    pathlib.Path("results").mkdir(parents=True, exist_ok=True)
    pathlib.Path("results/benchmark_report.json").write_text(
        json.dumps(report, indent=2) + "\n")
    v = report["deepeval"]
    print(f"Benchmark tree: {report['tree']}   run {report['run_id']}")
    print("deepeval: " + ("unavailable - " + str(v['error']) if not v["available"] else "present"))
    print(f"judge:    {report['judge_model'] or 'none (deterministic tiers only)'}\n")
    for d in report.get("datasets", []):
        print(f"  {d['suite']}/{d['use_case_id']}/{d['dataset_id']}  "
              f"{d['cases']} cases  gate={d['gate'].upper()}")
        if d["absorbed_by_normalisation"]:
            print(f"      format only, absorbed: {d['absorbed_by_normalisation']}")
        if d["real_differences"]:
            print(f"      real differences:      {d['real_differences']}")
    print(f"\n{report['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
