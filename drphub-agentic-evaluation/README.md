# Evaluating an agentic system

Point this at a folder of test cases and it returns correctness, latency and
token usage for the system that answers them. Correctness comes from
[DeepEval](https://github.com/confident-ai/deepeval), latency from
OpenTelemetry spans around each invocation, tokens from a small
OpenAI-compatible proxy that ships here and sits between your agent and its
model. It runs on your machine or on REANA and gives the same numbers either
way.

```
evaluate.py                the harness
counting_proxy.py          counts tokens between an agent and its model
reana.yaml                 card: evaluates a folder in this repository
reana-dcache.yaml          card: evaluates a folder fetched from dCache
fetch_dcache.py            pulls a benchmark tree off dCache
push_dcache.py             uploads one, checking it first
examples/answers_in_file/  cases with the answers already in the file
examples/with_agent/       cases the harness answers by running an agent
```

## Quick start

No credentials needed for this one.

```bash
python3 evaluate.py --tree examples/answers_in_file
```

```
  DATASET              CASES  PASS  GATE   LATENCY
  answers_in_file          3     2  fail   not measured
```

Three cases showing the three outcomes: an exact match, a match that only
differs in capitalisation and passes after normalisation, and one real
difference. `fail` is the correct verdict.

## Your own test cases

A benchmark is a folder. The harness searches it at any depth for files named
`cases.jsonl`, so an existing layout needs no rearranging.

**`cases.jsonl`, required.** One JSON object per line, using DeepEval's
`Golden` field names:

```json
{"name": "xrr_acronym", "input": "Three-letter acronym for X-ray reflectivity?", "expected_output": "XRR"}
```

Add `actual_output` if you already ran your system and just want the answers
graded. A case without `expected_output` is never counted, as pass or fail.

**`system.json`, optional.** Add it when you want the harness to run your
system, which is what makes latency and token measurement possible:

| `kind` | Needs | Use when |
| --- | --- | --- |
| `subprocess` | `command` | a script or binary |
| `python_entrypoint` | `entrypoint` as `module:callable` | an importable package |
| `http_endpoint` | `base` | already a service |

Your system receives one case as JSON on stdin and prints one JSON object:

```python
import json, sys
case = json.load(sys.stdin)
print(json.dumps({"output": my_agent(case["input"])}))
```

Relative paths in `system.json` resolve against the folder it sits in.
`examples/with_agent/` is a complete working instance to copy from.

**Where the path goes.** Locally, `--tree <folder>`. On REANA, the `tree`
parameter in `reana.yaml`, and the folder listed under `inputs.directories`
so it travels with the card.

## Running your system

```bash
export EVAL_LLM_BASE_URL=https://api.helmholtz-blablador.fz-juelich.de/v1
export EVAL_LLM_API_KEY=<key>
export EVAL_LLM_MODEL=<model>
python3 evaluate.py --tree examples/with_agent --repeats 3 --allow-exec
```

`--allow-exec` is required whenever a `system.json` would run a command or
import a module, because the job holds model and storage credentials. Leave
it off for a bundle you did not write. The harness then scores whatever
answers are in the file and says what it declined to run.

`--repeats` defaults to 1. Three shows a spread. A reliability claim needs
ten or more above temperature zero.

## Settings

| Variable | Meaning |
| --- | --- |
| `EVAL_LLM_BASE_URL` | model endpoint, forwarded exactly as written |
| `EVAL_LLM_API_KEY` | its credential |
| `EVAL_LLM_MODEL` | model identifier |
| `EVAL_LLM_AUTH_HEADER` | `Authorization` by default, `x-api-key` behind some proxies |
| `DCACHE_BEARER_TOKEN` | only for the dCache route below |

Your agent sees `OPENAI_BASE_URL` pointing at the counting proxy. The harness
sets it, so token counting asks nothing of your code. Leave
`EVAL_LLM_BASE_URL` unset and tokens are reported as `not_available` rather
than zero.

## Reading the output

```
  DATASET                                  CASES  PASS  GATE   LATENCY
  c5_agent_demo/reflectivity_qa                3     3  pass   355.9 ms
  c5_metadata_suggestion/p08_reflectivity     10     8  fail   not measured

  tokens   1,284 over 9 model calls (measured)
  gate     1 pass · 1 fail
```

`PASS` shows `-` when nothing was scored. `GATE` is `pass`, `fail`, or
`not scored`, and the third one matters: an unscored case and a wrong answer
are different facts. `LATENCY` reads `not measured` for any dataset whose
answers were already in the file.

Every number in `results/eval_report.json` carries how it was obtained:
`measured` by the harness, `self_reported` by the case, or `not_available`.
A model call whose usage block was missing is reported as unmeasured, with
the first bytes of the upstream reply printed so the cause is on screen.

A failing gate is often the right answer. In the second row above, eight of
ten fields match, one differs only in timestamp format and is absorbed by
normalisation, and `datasetName` genuinely differs. Green there would mean
the comparison was not looking.

Only the deterministic tiers gate. DeepEval's judged metrics need a judge
model and move when the judge does, so they are reported and never decide.

## On REANA

```bash
reana-client secrets-add --env EVAL_LLM_API_KEY=<key>
reana-client run -f reana.yaml -w my-eval
```

The DeepEval install takes most of eight minutes on the cluster. If it fails,
the run continues on the deterministic tiers and the report header says
`deepeval absent`.

## From dCache, optional

If your benchmark lives on the PUNCH dCache space, a REANA job can fetch and
score it with no copy in this repository. This needs `DCACHE_BEARER_TOKEN`
in your REANA secret store. How to get one is in
[drphub-hifis-dcache-token](https://gitlab-p4n.aip.de/physicsllm/drphub-hifis-dcache-token).

Upload with your own token, checking the bundle first:

```bash
DCACHE_BEARER_TOKEN="$(oidc-token HIFIS)" python3 push_dcache.py \
  --source ./my_bundle \
  --dest https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks/my_group/my_bundle \
  --dry-run
```

Drop `--dry-run` to write. Then run the dCache card against that path:

```bash
reana-client run -f reana-dcache.yaml -w my-eval \
  -p dcache_base=https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks/my_group
```

`reana-dcache.yaml` never passes `--allow-exec`, because the tree came off
shared storage where anyone in the VO could have written the `system.json`.

## Limits

A system carrying its own in-process model never reaches the proxy, so its
tokens read `not_available`. There is no trajectory capture, the whole
invocation is timed as one span. Statistics stop at mean and standard
deviation.

*Physics-LLM (BMBF ErUM-Data), work package C.5. CC BY 4.0.*
