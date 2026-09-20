# C5 metadata gate self-test — GIXOS pseudo-reflectivity (PETRA III P08)

**Does an automated metadata suggestion get caught when it invents a value?**

This card answers that with a controlled comparison. The same benchmark bundle
is scored twice against a **published SciCat record used unmodified as the
answer key** — once against faithful predictions, once against predictions that
truncate one field and fabricate another. The deterministic gate passes the
first and fails the second.

![result](results/figure.svg)

## What it runs

One bundle, six cases, two arms.

| | faithful | fabricated |
|---|---|---|
| `owner` | ✓ | ✓ |
| `contactEmail` | ✓ | ✓ |
| `type` | ✓ | ✓ |
| `creationLocation` | ✓ | ✗ truncated to `PETRA III` |
| `run_description` | ✓ `unknown` | ✗ invented `GIXOS calibration run at 295K` |
| **verdict** | **pass 5/5, exit 0** | **fail 3/5, exit 1** |

`run_description` **is not present in the published record.** The correct
answer is therefore a refusal — `unknown` — and the fabricated arm's answer is
plausible, well-formed and wrong. That case is the point of the card.

The sixth case, `description`, is free text. It is deliberately *not*
exact-matched, and is the only case a judged metric is asked about.

## Ground truth

SciCat `public-data/cb2f2cf8-3b59-4592-a383-706c2c91cfc4` — GIXOS-derived
pseudo reflectivity of a DPPG monolayer on tris-EDTA buffer (pH 7.3) at
35 mN/m, 295 K, measured at PETRA III P08 on the Langmuir trough GID setup.

The record is included verbatim at
`golden_label/scicat_dataset_metadata.json`. It is a real published record, not
a hand-written answer key — which is what makes "it did not fabricate" a
measurement rather than a claim.

## Why the judge is kept out of the verdict

The bundle declares its metrics in three tiers:

```
gates.required      exit_code_zero, workflow_terminal_status,
                    workflow_audit_paths, forbidden_values_absent,
                    expected_field_exact_match
gates.optional      workflow_semantic_closeness_pass, runtime_within_budget
gates.research_only deepeval
```

`deepeval` sits in `research_only` and never in `required`. An LLM judge can
inform a result but must never decide whether an upload passed.
`validate_bundle.py` enforces this: a bundle listing `deepeval` under
`metrics.required` is **rejected**.

`judge_model` is intentionally empty. A judge is a deployment choice, and a
bundle that hard-codes one stops being portable. With none configured the
harness reports the judged half as skipped, with the reason, and the gate still
runs — which is what happened in both arms here.

## Reproducibility

- **No network, no secrets, no custom image.** Python standard library only, on
  the stock `python:3.12-slim`. The figure is emitted as SVG by hand rather
  than via matplotlib, so the card has no dependencies at all.
- **Deterministic.** No sampling, no model call, no clock dependence. Two runs
  of this card produce byte-identical `evaluation.json`.
- **Self-validating.** `validate_bundle.py` checks the manifest is complete,
  that every declared file is present, and records a SHA-256 for each, so a run
  can state which bytes it used.
- **Packaged as RO-Crate** (`ro-crate/metadata.json`) with a maDMP export hook
  declared in the manifest.
- For a fully pinned run, replace the image tag in `reana.yaml` with its digest
  form and record the digest.

## What this card does and does not show

**Shows:** that the bundle format validates, that the deterministic gate
discriminates a faithful run from a fabricating one against real ground truth,
and that no judge model is involved in reaching that verdict.

**Does not show:** the metadata-suggestion workflow itself. The two prediction
sets are fixtures standing in for "what a faithful model said" and "what a
fabricating model said". This card tests **the gate, not the model.** The
companion card that runs the real workflow is
`02-rdm-publish-preview`.

## Layout

```
manifest.json                 physicsllm.upload_manifest.v1
datasets/cases.jsonl          six cases
contexts/project_context.json provenance; contains no scored answers
golden_label/                 the published record, verbatim
metrics/metric_set.json       the three tiers
ro-crate/metadata.json        RO-Crate 1.1
validate_bundle.py            the bundle gate
run_eval.py                   the scoring gate
demo.py                       runs both arms and compares
render_figure.py              SVG, standard library only
```

## Licence

Code and bundle: CC-BY-4.0, see `LICENSE`. The SciCat record it cites is
published open access by its depositors.
