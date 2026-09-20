"""Card 4 - score suggested metadata against a published record.

Takes a bundle in `physicsllm.upload_manifest.v1` form (the one card 1
validates and uploads), runs the deterministic comparison that gates, and then
- separately, and only for the cases that ask for it - a DeepEval judged
metric that does not gate.

The two are kept apart on purpose. `expected_field_exact_match` decides pass
or fail. DeepEval reports a score beside it. A judge model can never flip the
verdict, which is the property the bundle format exists to hold.

Without a judge model configured, or without the TestingPipeline enricher
installed, the judged half is reported as skipped and the gate still runs.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys


def load_cases(bundle: pathlib.Path, manifest: dict) -> list[dict]:
    rel = manifest.get("dataset", {}).get("path", "datasets/cases.jsonl")
    return [
        json.loads(line)
        for line in (bundle / rel).read_text().splitlines()
        if line.strip()
    ]


def load_predictions(path: pathlib.Path | None) -> dict[str, str]:
    """Predictions are {case_id: suggested_value}.

    Absent a predictions file we do not invent values - every case is recorded
    as having no prediction, which fails the gate honestly rather than
    reporting a score nobody produced.
    """
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text())


def deterministic_gate(cases: list[dict], predictions: dict[str, str]) -> dict:
    rows = []
    for case in cases:
        if case.get("scoring") != "exact":
            continue
        case_id = case["case_id"]
        predicted = predictions.get(case_id)
        expected = case["expected"]
        rows.append(
            {
                "case_id": case_id,
                "field": case.get("field"),
                "expected": expected,
                "predicted": predicted,
                "match": predicted == expected,
                "predicted_present": predicted is not None,
            }
        )
    passed = sum(1 for row in rows if row["match"])
    return {
        "metric": "expected_field_exact_match",
        "gating": True,
        "cases": rows,
        "passed": passed,
        "total": len(rows),
        "ok": len(rows) > 0 and passed == len(rows),
    }


def judged_metric(bundle: pathlib.Path, cases: list[dict],
                  metric_set: dict, run_dir: pathlib.Path) -> dict:
    spec = metric_set.get("deepeval", {})
    applies = set(spec.get("applies_to_cases", []))
    judged_cases = [case for case in cases if case["case_id"] in applies]

    if not judged_cases:
        return {"metric": "deepeval", "gating": False, "skipped": True,
                "reason": "no cases declare a judged metric"}

    judge = spec.get("judge_model") or os.environ.get("DEEPEVAL_JUDGE_MODEL", "")
    if not judge:
        return {
            "metric": "deepeval", "gating": False, "skipped": True,
            "reason": "no judge_model configured (metric_set.deepeval.judge_model or "
                      "DEEPEVAL_JUDGE_MODEL) - a judge is a deployment choice, not a bundle property",
            "would_have_judged": sorted(applies),
        }

    try:
        from physics_llm_platform.scoring import enrich_run_with_deepeval
    except ImportError:
        return {"metric": "deepeval", "gating": False, "skipped": True,
                "reason": "physics_llm_platform not importable in this environment",
                "would_have_judged": sorted(applies)}

    result = enrich_run_with_deepeval(
        str(run_dir),
        dataset=str(bundle / metric_set.get("dataset_path", "datasets/cases.jsonl")),
        deepeval_config=str(bundle / "metrics/metric_set.json"),
        require=False,
    )
    return {"metric": "deepeval", "gating": False, "judge_model": judge, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default=".", help="bundle root (card 1 format)")
    parser.add_argument("--predictions", default=None,
                        help="JSON {case_id: suggested_value} from a workflow run")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    bundle = pathlib.Path(args.bundle).resolve()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((bundle / "manifest.json").read_text())
    metric_set = json.loads((bundle / manifest["metrics"]["path"]).read_text())
    cases = load_cases(bundle, manifest)
    predictions = load_predictions(
        pathlib.Path(args.predictions) if args.predictions else None
    )

    gate = deterministic_gate(cases, predictions)
    judged = judged_metric(bundle, cases, metric_set, out)

    report = {
        "upload_id": manifest["upload_id"],
        "ground_truth": manifest.get("ground_truth", {}).get("source"),
        "predictions_supplied": bool(predictions),
        "gate": gate,
        "research_only": judged,
        "verdict": "pass" if gate["ok"] else "fail",
        "note": "verdict comes from the deterministic gate alone; the judged metric is reported beside it",
    }

    (out / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

    if not predictions:
        print(
            "\nNo predictions supplied - the harness ran and the gate is wired, "
            "but nothing was scored. Point --predictions at a workflow run.",
            file=sys.stderr,
        )
    return 0 if gate["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
