# DeepEval on A_Data — real output vs. a published record

Neither side of this comparison is a fixture. The prediction is
`mapped_header_draft.json` exactly as our extraction produced it; the answer
key is the published SciCat record for the same dataset,
`public-data/cb2f2cf8-3b59-4592-a383-706c2c91cfc4`.

## What it found

| field | result |
| --- | --- |
| 8 of 10 fields | identical |
| `creationTime` | differs in format only (`…10.000Z` vs `…10Z`) — **absorbed by normalisation** |
| `datasetName` | the draft dropped the qualifier **“GIXOS-derived”** — real content loss |

That contrast is the reason the metrics are tiered. A timestamp that differs
only in precision must not fail a run. A name that quietly loses a qualifier
must. No normaliser can be trusted to tell those apart on its own, which is
where a judged metric earns its place — reported, never deciding.

## The judged tier, actually run

Two different models, asked independently, on 2026-09-20:

| judge | `datasetName` verdict | score | reason given |
| --- | --- | --- | --- |
| `coding` | degraded | 0.7 | drops “GIXOS-derived”, which specifies the measurement origin |
| `desy-assistant` | degraded | 0.8 | the prefix is omitted, altering the specificity of the method |

Both agreed `creationTime` is **equivalent** — the same call the normaliser
makes — and both marked the other eight fields equivalent at 1.0. So the
judged tier reproduces across models and agrees with the deterministic gate,
while adding the one thing the gate cannot give: *why*, and *how badly*.

Cost: ~3.3k tokens for ten fields.

### A gateway caveat worth knowing

Requesting `vllm/reasoning` is silently served by `coding`. Verified by
asking for four model names in turn: `vllm/coding` → `coding`,
`vllm/desy-assistant` → `desy-assistant`, but `vllm/reasoning` → `coding`
and bare `reasoning` → `coding`. No error is raised.

This card therefore records `model_requested` **and** `model_served` on every
judged field, and prints a NOTE when they disagree — otherwise a result would
silently be attributed to the wrong model.

## Tiers

| tier | gating | what it is |
| --- | --- | --- |
| `exact` | yes | byte equality |
| `normalised` | yes | equality after timestamp and whitespace normalisation |
| `judged` | **never** | DeepEval `GEval`; skipped with a stated reason when no judge is configured |

`DEEPEVAL_JUDGE_MODEL` is intentionally unset. A judge is a deployment choice,
not a property of the bundle.

## Running it

No secrets, no harness image, no MCP server, no S4P — the two JSON files it
needs travel with the card, and it runs on stock `python:3.12-slim`.

```bash
pip install deepeval
python run_deepeval.py --data data --out results
```

DeepEval is installed at run time. If the job has no outbound network the
install fails, the report says so, and the same comparison is still computed —
so the card produces a verdict either way. Telemetry is opted out.
