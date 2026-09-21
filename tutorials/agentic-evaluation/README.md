# Evaluating an agentic system

Correctness with DeepEval, latency with OpenTelemetry, token usage with a
counting proxy that ships here. Runs on your machine, your REANA, or ours,
and gives the same numbers in each case.

Everything in this folder runs as it stands. The first step needs no
credentials.

```
evaluate.py                the harness
counting_proxy.py          counts tokens between an agent and its model
fetch_dcache.py            pulls a tree off dCache
push_dcache.py             puts one there, checking it first
reana-local.yaml           card: a folder in the repository
reana-dcache.yaml          card: a folder on dCache
examples/answers_in_file/  cases you already answered
examples/with_agent/       cases the harness answers by running an agent
```

## Quick start

```bash
python3 evaluate.py --tree examples/answers_in_file
```

```
  DATASET              CASES  PASS  GATE   LATENCY
  answers_in_file          3     2  fail   not measured
```

Three cases showing the three outcomes: an exact match, a match that
survives normalisation because only the capitalisation differs, and one real
difference. `fail` is correct.

## Running your system

`examples/with_agent/` carries a `system.json`, so the harness invokes an
agent instead of reading answers from the file. That needs a model:

```bash
export EVAL_LLM_BASE_URL=https://api.helmholtz-blablador.fz-juelich.de/v1
export EVAL_LLM_API_KEY=<key>
export EVAL_LLM_MODEL=<model>
python3 evaluate.py --tree examples/with_agent --repeats 3
```

```
eval harness   run local-20260921T183124Z   on local   3 repeats
  deepeval 4.2.3   opentelemetry 1.38.0   proxy 127.0.0.1:34945

  DATASET       CASES  PASS  GATE   LATENCY
  with_agent        3     3  pass   412 ± 38 ms

  tokens   1,284 over 9 model calls (measured)
  gate     1 pass
```

Point `--tree` at your own directory when you have one.

On REANA:

```bash
reana-client secrets-add --env EVAL_LLM_API_KEY=<key>
reana-client run -f reana-local.yaml -w my-eval
```

## From dCache

A group uploads a bundle to the VO space and a REANA job fetches and scores
it. Verified 2026-09-21: 42 files, no errors, same verdict as the local run.

### Upload

```bash
DCACHE_BEARER_TOKEN="$(oidc-token HIFIS)" python3 push_dcache.py \
  --source ./my_bundle \
  --dest https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks/my_group/my_bundle \
  --dry-run
```

```
  cases.jsonl: 10 case(s), 10 with a golden label
  dry run: 42 file(s) would be uploaded
```

Drop `--dry-run` to write. The bundle is checked first, so a malformed
`cases.jsonl` is caught here instead of eight minutes into a REANA run.

**Upload with your own token, not a macaroon.** Macaroons are read-only at
this door: one asking for `UPLOAD` is issued without complaint and refused at
write time. Your own token writes, which also means the upload carries your
identity.

### Read credential

```bash
M=$(curl -s -X POST \
  -H "Authorization: Bearer $(oidc-token HIFIS)" \
  -H 'Content-Type: application/macaroon-request' \
  -d '{"caveats":["path:/punch/physicsllm/01Benchmarks","activity:DOWNLOAD,LIST,READ_METADATA"],"validity":"PT168H"}' \
  https://dcache-doma-door01.desy.de/ \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['macaroon'])")
reana-client secrets-add --overwrite --env DCACHE_BEARER_TOKEN="$M" && unset M
```

**Post to the door root, nothing after the slash.** The URL you post to
becomes a path caveat of its own, and two path caveats permit that one
directory and nothing under it:

| Posted to | `PROPFIND` on a subdirectory |
| --- | --- |
| `https://door/punch/physicsllm/01Benchmarks/` | 403 |
| `https://door/` | 207 |

A 403 here looks like a permission problem on the data. Check the caveats;
two `path` lines means the wrong URL:

```bash
python3 -c "import base64,sys;t=sys.argv[1];print(base64.urlsafe_b64decode(t+'='*(-len(t)%4)).decode())" "$M" | grep path
```

### Run

```bash
reana-client run -f reana-dcache.yaml -w my-eval \
  -p dcache_base=https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks
```

## Reading the output

Every example below is from a run that happened.

```
42 file(s) fetched
```

The tree arrives with its structure intact, so what you uploaded is what gets
evaluated. `results/fetch_report.json` records the base URL, whether TLS
verified, and every file with its size.

```
  DATASET                                  CASES  PASS  GATE   LATENCY
  c5_agent_demo/reflectivity_qa                3     3  pass   355.9 ms
  c5_metadata_suggestion/p08_reflectivity     10     8  fail   not measured

  tokens   1,284 over 9 model calls (measured)
  gate     1 pass · 1 fail
```

The header line says what was actually loaded. If DeepEval failed to install
it reads `deepeval absent` and the run continues on the deterministic tiers.
`on reana` comes from `REANA_WORKFLOW_UUID`.

`PASS` shows `-` when nothing was scored. Zero would be a claim.

`GATE` is `pass`, `fail`, or `not scored`. The third value is the one that
matters: an unscored case and a wrong answer are different facts.

`LATENCY` reads `not measured` when nothing was invoked, which is every
dataset whose answers already sit in the file.

`tokens` counts what passed through the proxy, and when it cannot, it says
why and prints what came back:

```
  tokens   not measured, 3 model calls made, none returned a usage block
           upstream returned no usage block; body began: data: {"choices"...
```

Some endpoints stream, and a streamed reply carries usage only in a late
frame. The proxy reads those. When it still finds none, the cause is on
screen instead of guessed at.

**A failing gate is often the right answer.** For the P08 bundle, eight of
ten fields match, `creationTime` differs only in format and is absorbed by
normalisation, and `datasetName` genuinely differs. Green there would mean
the comparison was not looking.

`results/eval_report.json` holds per-case values, the full text of any error,
and the spans. Every measurement carries `measured`, `self_reported` or
`not_available`, so a latency you measured and one somebody typed never share
a column.

## The structure

### One required file

`cases.jsonl`, one JSON object per line, using DeepEval's `Golden` field
names so the file loads with `Golden(**row)` and needs no translation:

```json
{"name": "xrr_acronym", "input": "Three-letter acronym for X-ray reflectivity?", "expected_output": "XRR"}
```

Any directory containing one is found, at any depth, so your existing layout
needs no rearranging.

### One optional file

`system.json` says how to run your system:

| `kind` | Needs | Use when |
| --- | --- | --- |
| `subprocess` | `command` | a script or binary |
| `python_entrypoint` | `entrypoint` as `module:callable` | an importable package |
| `http_endpoint` | `base`, optionally `token_env` | already a service |

One case as JSON on stdin, one JSON object on stdout:

```python
import json, sys
case = json.load(sys.stdin)
print(json.dumps({"output": my_agent(case["input"])}))
```

Relative paths resolve against the dataset folder, so `agent.py` beside your
cases is found wherever the harness runs from.

## Golden labels

A case is scored when it has both `expected_output` and an answer.

| `expected_output` | `actual_output` | Outcome |
| --- | --- | --- |
| yes | yes | scored |
| yes | no, `system.json` present | your system runs, then scored |
| yes | no, no `system.json` | not scored |
| no | either | not scored, nothing inferred |

A case without a golden label is never counted as a failure.

## Turning measurement off

Each piece is independent, and off is a valid configuration.

| To drop | Do this | Reported as |
| --- | --- | --- |
| tokens | leave `EVAL_LLM_BASE_URL` unset | `not_available` |
| latency | omit `system.json` | `not_available` |
| judged metrics | nothing, they are off by default | absent |

Judged metrics never gate. Every agentic metric DeepEval ships calls a judge
model, so a judged score moves when the judge does. Everything that gates is
computed from what was observed.

## Settings

| Variable | Meaning |
| --- | --- |
| `EVAL_LLM_BASE_URL` | model endpoint, forwarded verbatim |
| `EVAL_LLM_API_KEY` | its credential |
| `EVAL_LLM_AUTH_HEADER` | `Authorization`, or `x-api-key` behind a proxy that consumes it |
| `EVAL_LLM_MODEL` | model identifier |
| `DCACHE_BEARER_TOKEN` | macaroon to read, your own token to write |

`EVAL_LLM_*` does not collide with `LLM_*`, `LOCAL_LLM_*` or `OPENAI_*`,
which this project already uses several hundred times. The harness reads only
`EVAL_LLM_*` and never falls back.

`EVAL_LLM_BASE_URL` is forwarded exactly as written. Open WebUI serves
`/api/chat/completions`, and appending an assumed path turns a working
endpoint into a 405.

Your agent sees `OPENAI_BASE_URL` pointing at the counting proxy on loopback.
The harness sets it, so token counting asks nothing of your code.

## Where the numbers come from

| Instrument | Boundary | Produces | Judged |
| --- | --- | --- | --- |
| Counting proxy | agent to model | tokens, per-call latency | no |
| OpenTelemetry | harness to system | step count, duration | no |
| DeepEval deterministic | answer to golden | exact, normalised | no |
| DeepEval agentic | trajectory | task completion, tool correctness | yes |

Measurement follows the instrument, not the machine, so running this
yourself produces the numbers we would. Running it on our infrastructure adds
assurance, and the report records which it was.

`--repeats` defaults to 1. One run cannot tell a reliable system from a lucky
one. Three shows a spread; ten above temperature zero is what a reliability
claim needs.

## Limits

A system carrying its own in-process model never reaches the proxy, so its
tokens are invisible and reported `not_available`.

There is no trajectory capture. The harness times the whole invocation, so an
agent that emits no spans is a black box between call and answer.

Statistics stop at mean and standard deviation. No confidence intervals, no
pass@k, no comparison against a baseline. This measures; it is not yet a
benchmark framework.
