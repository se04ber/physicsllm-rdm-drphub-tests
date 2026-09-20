"""Score a real metadata draft against a published SciCat record, with DeepEval.

Unlike the self-test card, neither side of this comparison is a fixture. The
prediction is `mapped_header_draft.json` as our own extraction produced it, and
the answer key is the published record for the same dataset,
`public-data/cb2f2cf8-3b59-4592-a383-706c2c91cfc4`.

Three metric tiers, and the split is the point:

  exact        deterministic, GATING  - byte equality
  normalised   deterministic, GATING  - equality after timestamp/whitespace
                                        normalisation, so a formatting
                                        difference stops being a failure
  judged       DeepEval GEval, NEVER GATING - needs an LLM judge; skipped with
                                        a stated reason when none is configured

The interesting cases live in the gap between the first two tiers and the
third. A timestamp that differs only in precision is a false alarm that
normalisation should absorb. A dataset name that silently drops a qualifier is
real content loss that no normaliser should forgive - and telling those two
apart is exactly what a judged metric is for, which is why it is reported and
never allowed to decide.

Always exits 0. Not having a judge is a finding, not a failed job.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys

# DeepEval phones home by default; a card must not, and may have no network.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("ERROR_REPORTING", "NO")

# Scored because each is a claim a person would rely on. scientificMetadata is
# deliberately excluded: it is a nested tree and needs its own comparison.
FIELDS = [
    "contactEmail",
    "owner",
    "principalInvestigator",
    "ownerGroup",
    "type",
    "isPublished",
    "creationLocation",
    "creationTime",
    "datasetName",
    "description",
]


def normalise(field: str, value):
    """Absorb differences that carry no meaning - and nothing else."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    if "time" in field.lower() or "date" in field.lower():
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone(datetime.timezone.utc).isoformat()
        except ValueError:
            pass
    return text


def load_metrics():
    """Import DeepEval, returning (module_bits, error). Never raises."""
    try:
        from deepeval.metrics import BaseMetric, GEval
        from deepeval.test_case import LLMTestCase
    except Exception as exc:  # noqa: BLE001 - any import failure is reportable
        return None, f"{type(exc).__name__}: {exc}"

    class DeterministicFieldMatch(BaseMetric):
        """A real DeepEval metric that consults no model.

        DeepEval is the harness here, not the judge: subclassing BaseMetric
        keeps these results in the same report as the judged ones while
        leaving the verdict computable and reproducible.
        """

        def __init__(self, field: str, strict: bool):
            self.threshold = 1.0
            self.field = field
            self.strict = strict
            self.evaluation_model = "deterministic"
            self.strict_mode = strict
            self.async_mode = False
            self.verbose_mode = False

        def measure(self, test_case) -> float:
            got, want = test_case.actual_output, test_case.expected_output
            if not self.strict:
                got, want = normalise(self.field, got), normalise(self.field, want)
            self.score = 1.0 if got == want else 0.0
            self.success = self.score >= self.threshold
            self.reason = (
                "identical" if self.success
                else f"expected {want!r}, got {got!r}"
            )
            return self.score

        async def a_measure(self, test_case, *_args, **_kwargs) -> float:
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return bool(getattr(self, "success", False))

        @property
        def __name__(self):  # noqa: A003 - DeepEval reads this for the report
            return f"{'exact' if self.strict else 'normalised'}:{self.field}"

    return (BaseMetric, GEval, LLMTestCase, DeterministicFieldMatch), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = pathlib.Path(args.data)

    golden = json.loads((data / "golden_label.json").read_text())
    draft = json.loads((data / "mapped_header_draft.json").read_text())
    source = json.loads((data / "SOURCE.json").read_text())

    report: dict = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dataset_pid": source.get("dataset_pid"),
        "prediction": "metadata_out/mapped_header_draft.json (real extraction output)",
        "ground_truth": "golden_label/scicat_dataset_metadata.json (published SciCat record)",
        "tiers": {"exact": "gating", "normalised": "gating", "judged": "research_only"},
        "fields": [],
    }

    bits, import_error = load_metrics()
    engine: dict[str, object] = {"available": bits is not None}
    report["deepeval"] = engine
    if import_error:
        engine["error"] = import_error
    else:
        import deepeval
        engine["version"] = getattr(deepeval, "__version__", "unknown")

    judge = os.environ.get("DEEPEVAL_JUDGE_MODEL", "").strip()
    report["judge_model"] = judge or None

    exact_fail, norm_fail = [], []

    for field in FIELDS:
        want, got = golden.get(field), draft.get(field)
        entry = {
            "field": field,
            "expected": want,
            "actual": got,
            "present_in_draft": field in draft,
        }

        if bits is not None:
            LLMTestCase, Det = bits[2], bits[3]
            case = LLMTestCase(
                input=f"What is the {field} of this dataset?",
                actual_output="" if got is None else str(got),
                expected_output="" if want is None else str(want),
            )
            strict = Det(field, strict=True)
            loose = Det(field, strict=False)
            strict.measure(case)
            loose.measure(case)
            entry["exact"] = {"score": strict.score, "pass": strict.is_successful(),
                              "reason": strict.reason}
            entry["normalised"] = {"score": loose.score, "pass": loose.is_successful(),
                                   "reason": loose.reason}
        else:
            # DeepEval unavailable: the same comparison, so the card still
            # reports a verdict rather than nothing.
            ok_exact = str(got) == str(want)
            ok_norm = normalise(field, got) == normalise(field, want)
            entry["exact"] = {"score": float(ok_exact), "pass": ok_exact,
                              "reason": "computed without DeepEval"}
            entry["normalised"] = {"score": float(ok_norm), "pass": ok_norm,
                                   "reason": "computed without DeepEval"}

        if not entry["exact"]["pass"]:
            exact_fail.append(field)
        if not entry["normalised"]["pass"]:
            norm_fail.append(field)

        if not judge:
            entry["judged"] = {"skipped": True,
                               "reason": "DEEPEVAL_JUDGE_MODEL unset - a judge is a "
                                         "deployment choice, not a bundle property"}
        else:
            # Research-only tier. It is scored on every field, including the
            # ones the deterministic gate already passed, so the two can be
            # compared - but it never contributes to the verdict.
            try:
                from judge import judge_field
                entry["judged"] = judge_field(field, want, got)
            except Exception as exc:  # noqa: BLE001 - a judge must never fail the card
                entry["judged"] = {"skipped": True, "reason": f"{type(exc).__name__}: {exc}"}
        report["fields"].append(entry)

    report["summary"] = {
        "scored": len(FIELDS),
        "exact_failures": exact_fail,
        "normalised_failures": norm_fail,
        "absorbed_by_normalisation": [f for f in exact_fail if f not in norm_fail],
        "real_content_differences": norm_fail,
        "gate": "pass" if not norm_fail else "fail",
    }
    (out / "deepeval_report.json").write_text(json.dumps(report, indent=2) + "\n")

    # What a person reads in the REANA log.
    print(f"DeepEval on A_Data · {report['dataset_pid']}")
    version = engine.get("version")
    print("deepeval: " + (f"v{version}" if version
                          else f"UNAVAILABLE - {engine.get('error')}"))
    print(f"judge:    {judge or 'none (judged tier skipped, by design)'}\n")
    for e in report["fields"]:
        mark = "ok  " if e["normalised"]["pass"] else "FAIL"
        note = "" if e["exact"]["pass"] else (
            "  (format only - absorbed)" if e["normalised"]["pass"] else "  <- content differs")
        print(f"  {mark} {e['field']}{note}")
    s = report["summary"]
    print(f"\nexact failures:      {s['exact_failures'] or 'none'}")
    print(f"absorbed by norm.:   {s['absorbed_by_normalisation'] or 'none'}")
    print(f"real differences:    {s['real_content_differences'] or 'none'}")
    print(f"deterministic gate:  {s['gate'].upper()}")

    scored = [e for e in report["fields"]
              if isinstance(e.get("judged"), dict) and e["judged"].get("usable")]
    if scored:
        served = sorted({str(e["judged"].get("model_served")) for e in scored})
        asked = sorted({str(e["judged"].get("model_requested")) for e in scored})
        print(f"\njudged tier — research only, never gating")
        print(f"  requested {asked} · served {served}")
        if set(asked) != {f"vllm/{m}" for m in served} and served != asked:
            print("  NOTE: the gateway did not serve the model that was asked for.")
        for e in scored:
            j = e["judged"]
            mark = "" if e["normalised"]["pass"] else "   <- deterministic gate said FAIL"
            print(f"  {e['field']:<22} {str(j.get('verdict')):<11} {j.get('score')}{mark}")
            if not e["normalised"]["pass"]:
                print(f"      judge: {j.get('reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
