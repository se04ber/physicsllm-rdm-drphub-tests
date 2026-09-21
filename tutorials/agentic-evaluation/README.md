# Evaluating an agentic system

Correctness with DeepEval, latency with OpenTelemetry, token usage with a
counting proxy that ships with the harness. Runs on your machine, your REANA,
or ours, and produces the same numbers in each case.

Everything in this folder runs as it stands. Start with Route A, which needs no
credentials at all.

```
tutorials/agentic-evaluation/
  README.md
  eval_harness.py            the harness
  counting_proxy.py          counts tokens between your agent and its model
  fetch_dcache.py            Route B only: pulls a tree off dCache
  reana-local.yaml           Route A card
  reana-dcache.yaml          Route B card
  example/my_benchmark/
    cases.jsonl              the questions and the right answers
    system.json              how to run your system (optional)
    my_agent.py              the smallest system that can be measured
```

---

## Route A: a folder you have

### 1. Score answers you already have

No credentials, no model, no setup:

```bash
python3 eval_harness.py --tree example/already_answered
```

```
  DATASET                                CASES  PASS  GATE       LATENCY
  already_answered                           3     2  fail       not measured
```

Three cases, chosen to show all three outcomes: one exact match, one that
passes on normalisation because only the capitalisation differs, and one real
difference. The gate is `fail`, correctly, on one case out of three. Latency
and tokens are `not_available`, because nothing ran.

This is the lowest bar the harness has, and a complete use of it.

### 2. Have the harness run your system

`example/my_benchmark` carries a `system.json`, so the harness invokes an agent
instead of reading answers from the file. That needs a model:

```bash
export EVAL_LLM_BASE_URL=https://api.helmholtz-blablador.fz-juelich.de/v1
export EVAL_LLM_API_KEY=<your key>
export EVAL_LLM_MODEL=<model name>
python3 eval_harness.py --tree example/my_benchmark --repeats 3
```

```
eval harness   run local-20260921T183124Z   on local   3 repeats
  deepeval 4.2.3   opentelemetry 1.38.0   proxy 127.0.0.1:34945

  DATASET                                CASES  PASS  GATE       LATENCY
  my_benchmark                               3     3  pass       412 ± 38 ms

  tokens   1,284 over 9 model calls (measured)
  gate     1 pass

  results/eval_report.json
```

### 3. Replace it with your own

Point `--tree` at your directory. Any directory containing `cases.jsonl` is
found, at any depth, so your existing layout needs no rearranging.

### 4. Run it on REANA

```bash
reana-client secrets-add --env EVAL_LLM_API_KEY=<your key>
reana-client run -f reana-local.yaml -w my-eval
```

---

## Route B: a folder on dCache

The community path. A group uploads a bundle to the VO space, and a REANA job
fetches and scores it. No copy lives in this repository, and no manual step
sits between the upload and the verdict.

### 1. Upload your benchmark

`push_dcache.py` needs nothing installed beyond Python. It checks the bundle
before it writes anything, because a malformed `cases.jsonl` that reaches the
store costs a round trip through a REANA run to discover:

```bash
DCACHE_BEARER_TOKEN="$(oidc-token HIFIS)" python3 push_dcache.py \
  --source ./my_benchmark \
  --dest https://dcache-doma-door01.desy.de/punch/physicsllm/user/<your-account>/Benchmarks/my_benchmark \
  --dry-run
```

**The upload uses your own Helmholtz token, not a macaroon.** Measured on
2026-09-21: macaroons are read-only at this door. One requesting
`activity:UPLOAD` is issued without complaint and then refused at write time
with `Permission denied for PUT`, whether it was minted by password or
through the OIDC identity. Your own token writes: `PUT` returns 201, and a
42-file bundle uploaded with nothing refused.

That split is worth keeping on purpose. You write as yourself, so the upload
carries real attribution and no service acts on your behalf. The macaroon in
step 3 reads, and it sits in a REANA secret store where it cannot write.

```
  cases.jsonl: 10 case(s), 10 with a golden label
  dry run: 42 file(s) would be uploaded
```

Drop `--dry-run` to upload.

Write into your own `user/<account>/` tree. `01Benchmarks` is the obvious
place for shared bundles and an ordinary VO member cannot write there:
listing works and every `PUT` is refused. Whether that becomes a
group-writable drop area is a question for the VO administrators.

A bundle with no `expected_output` anywhere is reported and still uploaded.
Reference-free is a legitimate shape; it simply cannot be scored against
goldens, and you should know that before the run rather than after.

**The rclone helper still works** and is the right tool for large transfers,
since it handles parallelism and retries. It needs rclone, `oidc-agent`, a
per-user config and intranet access. For a benchmark bundle, a macaroon and
this script need none of those.

```bash
s4p upload --profile custom --source ./my_benchmark --dest-rel 01Benchmarks/my_group/my_benchmark
```

### 2. Mint a read-only macaroon

```bash
curl -s -X POST \
  -H 'Content-Type: application/macaroon-request' \
  -d '{"caveats":["path:/punch/physicsllm/01Benchmarks","activity:DOWNLOAD,LIST,READ_METADATA"],"validity":"PT168H"}' \
  -u <your-desy-account> \
  https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks/ \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['macaroon'])"
```

Three choices in that request are deliberate. The path caveat narrows the
credential to `01Benchmarks`, so it cannot read anyone's user tree. The
activity list is read-only, so a fault in the card cannot write or delete.
`PT168H` is a week. A macaroon can only ever narrow what the person minting it
already holds.

**Why a macaroon and not a PUNCH token.** Helmholtz AAI silently drops
`storage.create` and `storage.modify` from the grant: the returned token looks
healthy, carries only the read half, and the write is refused. It also issues
`aud: public-oidc-agent` rather than a storage resource, and offers no
token-exchange grant. Until those gaps close, a macaroon minted at the door is
the credential that works, and it is better bounded than an IdP token would be.
When PUNCH grants the scopes, this step changes and nothing else does.

### 3. Store it and run

```bash
read -rsp 'macaroon: ' K && reana-client secrets-add --overwrite --env DCACHE_BEARER_TOKEN="$K" && unset K
reana-client secrets-add --env EVAL_LLM_API_KEY=<your key>
reana-client run -f reana-dcache.yaml -w my-eval
```

The card fetches the tree, preserving its structure, then evaluates it. Without
the token it reports "no token" and exits 0, because a card that is not set up
yet is a finding rather than a failure.

---

## The structure, and why it is this small

### One required file

`cases.jsonl`, one JSON object per line, using DeepEval's own `Golden` field
names so the file loads with `Golden(**row)` and needs no translation layer:

```json
{"name": "xrr_acronym", "input": "Three-letter acronym for X-ray reflectivity?", "expected_output": "XRR"}
```

Nothing else is required. The bar is deliberately one file: the layout below it
encodes our taxonomy, and asking a collaborator to learn it before they can be
scored is a cost with no return.

### One optional file

`system.json` says how to run your system. Add it only when you want the
harness to generate the answers, which is what makes latency and tokens
measurable.

| `kind` | Needs | Use when |
| --- | --- | --- |
| `subprocess` | `command` | Your system is a script or binary |
| `python_entrypoint` | `entrypoint` as `module:callable` | Your system is an importable package |
| `http_endpoint` | `base`, optionally `token_env` | Your system is already a service |

The contract is one case as JSON on stdin, one JSON object on stdout:

```python
import json, sys
case = json.load(sys.stdin)
print(json.dumps({"output": my_agent(case["input"])}))
```

Your code lives wherever the command can reach it. The harness does not import
it, inspect it, or require a framework.

---

## Golden labels decide what can be reported

A case is scored when it has both `expected_output` and an answer. The answer
comes from `actual_output` in the file, or from running your system.

| `expected_output` | `actual_output` | Outcome |
| --- | --- | --- |
| present | present | scored, counts toward the gate |
| present | absent, `system.json` present | your system runs, then it is scored |
| present | absent, no `system.json` | not scored, and reported as such |
| absent | either | not scored, and nothing is inferred |

A case without a golden label is never counted as a failure. The gate has three
values, and "could not be scored" is one of them. An unscored case and a wrong
answer are different facts, and a report that conflates them cannot be acted on.

---

## Turning measurement off

Each piece is independent, and off is a valid configuration.

**Tokens.** Leave `EVAL_LLM_BASE_URL` unset. No proxy starts and tokens are
reported `not_available`, never zero. Zero is a claim; absence is not.

**Latency.** Omit `system.json`. Nothing is invoked, so there is nothing to
time.

**Judged metrics.** Off by default, and they never gate. Every agentic metric
DeepEval ships calls a judge model, so a judged score moves when the judge
does. Everything that gates here is computed from what was observed.

**All of it.** A `cases.jsonl` with `actual_output` already filled in gives
correctness only, and that is a complete use of the harness.

---

## Settings

| Variable | Meaning |
| --- | --- |
| `EVAL_LLM_BASE_URL` | Model endpoint, forwarded verbatim |
| `EVAL_LLM_API_KEY` | Its credential |
| `EVAL_LLM_AUTH_HEADER` | Header carrying it. `Authorization` by default; `x-api-key` behind a reverse proxy that already consumes it |
| `EVAL_LLM_MODEL` | Model identifier |
| `DCACHE_BEARER_TOKEN` | Route B only: the macaroon |

These names do not collide with `LLM_*`, `LOCAL_LLM_*` or `OPENAI_*`, which are
already used several hundred times across this project. The harness reads only
`EVAL_LLM_*` and never falls back, because a fallback chain reintroduces the
collision the names avoid.

`EVAL_LLM_BASE_URL` is forwarded exactly as written. Open WebUI serves
`/api/chat/completions` rather than `/v1/chat/completions`, and appending an
assumed path turns a working endpoint into a 405.

Your agent sees `OPENAI_BASE_URL` pointing at the counting proxy on loopback.
The harness sets it. Your code needs no knowledge of where the model actually
lives, which is why token counting requires nothing of you.

---

## Where the numbers come from

| Instrument | Boundary | Produces | Judged |
| --- | --- | --- | --- |
| Counting proxy | Between your agent and its model | Tokens, per-call latency | no |
| OpenTelemetry | The harness invoking your system | Step count, per-step duration | no |
| DeepEval deterministic | The answer against the golden | Exact and normalised match | no |
| DeepEval agentic | The trajectory | Task completion, tool correctness | **yes** |

Every number carries how it was obtained: `measured`, `self_reported`, or
`not_available`. The JSON report holds per-case detail, the full text of any
error, and the spans.

Measurement follows the instrument rather than the machine, so running the
harness yourself produces the same numbers we would. What running it on our
infrastructure adds is assurance, not capability, and the report records which
it was.

`--repeats` defaults to 1 and should not stay there for anything you intend to
report. One run cannot distinguish a system that is reliable from one that was
lucky. Three shows a spread; ten above temperature zero is what a reliability
claim needs.

Two limits worth stating. A system carrying its own in-process model never
reaches the proxy, so its tokens are invisible and reported `not_available`.
And a streamed response returns no usage block unless the request asks for one,
which the proxy does on every call it forwards.
