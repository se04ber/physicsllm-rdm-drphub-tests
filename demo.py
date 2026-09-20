"""Card 0 - the gate, shown discriminating.

Scores the same bundle twice: once against faithful predictions, once against
predictions that truncate one field and invent another. Reports both, and
exits 0 either way, because the point of the card is the *contrast* - a run
that only ever passes proves nothing about a gate.

Nothing here reaches the network, needs a secret, or pulls an image.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent

FAITHFUL = {
    "reflectivity_gixos_p08_owner": "Chen Shen",
    "reflectivity_gixos_p08_contact": "chen.shen@desy.de",
    "reflectivity_gixos_p08_type": "raw",
    "reflectivity_gixos_p08_creation_location":
        "Deutsches Elektronen-Synchrotron DESY / PETRA III / P08 / Langmuir trough GID setup",
    # The field is absent from the published record. "unknown" is the correct
    # answer, and refusing to guess is the behaviour being tested.
    "reflectivity_gixos_p08_absent_field": "unknown",
}

FABRICATED = {
    **FAITHFUL,
    # Truncated: right facility, wrong answer.
    "reflectivity_gixos_p08_creation_location": "PETRA III",
    # Invented outright: plausible, well-formed, and not in the record.
    "reflectivity_gixos_p08_absent_field": "GIXOS calibration run at 295K",
}


def score(name: str, predictions: dict[str, str], out: pathlib.Path) -> dict:
    preds_path = out / f"predictions_{name}.json"
    preds_path.write_text(json.dumps(predictions, indent=2) + "\n")
    run_out = out / name
    completed = subprocess.run(
        [sys.executable, str(HERE / "run_eval.py"),
         "--bundle", str(HERE),
         "--predictions", str(preds_path),
         "--out", str(run_out)],
        capture_output=True, text=True,
    )
    report = json.loads((run_out / "evaluation.json").read_text())
    return {
        "arm": name,
        "exit_code": completed.returncode,
        "verdict": report["verdict"],
        "passed": report["gate"]["passed"],
        "total": report["gate"]["total"],
        "failures": [
            {"field": row["field"], "expected": row["expected"], "predicted": row["predicted"]}
            for row in report["gate"]["cases"] if not row["match"]
        ],
        "judged_metric": report["research_only"].get("reason", "ran"),
    }


def main() -> int:
    out = pathlib.Path("results")
    out.mkdir(parents=True, exist_ok=True)

    validation = subprocess.run(
        [sys.executable, str(HERE / "validate_bundle.py"),
         "--bundle", str(HERE), "--out", str(out / "validation")],
        capture_output=True, text=True,
    )

    summary = {
        "bundle_validation_exit": validation.returncode,
        "ground_truth": "SciCat public-data/cb2f2cf8-3b59-4592-a383-706c2c91cfc4 (published record, used unmodified)",
        "arms": [
            score("faithful", FAITHFUL, out),
            score("fabricated", FABRICATED, out),
        ],
    }

    faithful, fabricated = summary["arms"]
    summary["gate_discriminates"] = (
        faithful["verdict"] == "pass" and fabricated["verdict"] == "fail"
    )
    summary["conclusion"] = (
        "The deterministic gate passed a faithful run and failed one that invented a value "
        "for a field absent from the published record. No judge model was involved in either "
        "verdict."
        if summary["gate_discriminates"]
        else "Gate did not discriminate - investigate before showing this."
    )

    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
