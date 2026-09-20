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
