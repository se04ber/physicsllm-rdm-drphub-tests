# Running these in front of people

Every timing and every output below is from a real run on
`reana-p4n.aip.de` on 2026-09-21. Nothing here is illustrative.

## Setup, once

```bash
source ~/Work/Project/REANA-TEST/activate_reana_env.sh
reana-client ping
```

## The two to show

### 1. The MCP server and its evaluation, together, in 19 seconds

```bash
reana-client run -f reana-mcp-selfcontained.yaml -w demo-mcp
```

The card brings the server with it, starts it on loopback and measures
against it. Nineteen seconds end to end, because it installs nothing.

```
endpoint      http://127.0.0.1:8000/mcp
session       established
tool surface  the live surface matches the nine documented read-only tools
datasets      23/23 scored, gate=pass
otel          27 calls, 205.9 ms
```

**What to say about latency.** Roughly 2 ms per call here against roughly
95 ms to the deployed server. That is a contrast, not an improvement: one
measures the server, the other measures the path to it.

**If asked why the server travels with the card.** Because the deployed one
is not publicly reachable, which is deliberate. A run on the default backend
reached the install step, then failed the handshake with
`[Errno 101] Network is unreachable` after a 90-second timeout. The backend
that can reach it, compute4punch, accepts no image but `wlcg-wn:latest`, so a
card cannot bring its own there either.

### 2. Measuring an agentic system

```bash
reana-client run -f reana-eval-harness.yaml -w demo-eval
```

Seven and a half minutes, nearly all of it the DeepEval install. Invokes the
agent, times it, and counts its model calls through a proxy that ships in the
card.

```
  DATASET                                  CASES  PASS  GATE   LATENCY
  c5_agent_demo/reflectivity_qa                3     3  pass   355.9 ms
  c5_metadata_suggestion/p08_reflectivity     10     8  fail   not measured
```

**The one to have an answer ready for.** If tokens read `not measured`, the
line underneath says what the upstream actually returned. The endpoint has
answered 200 with no usage block, and the harness now reads usage out of
streamed frames, so a streaming provider is handled. Anything else is named
on screen rather than guessed at.

**`p08_reflectivity` failing is correct.** Eight of ten fields match, one
difference is absorbed by normalisation, and `datasetName` is a real
difference. A green gate there would mean the comparison was not looking.

## Timings, measured

| Card | Duration | Why |
| --- | --- | --- |
| `reana-mcp-selfcontained.yaml` | 19 s | installs nothing |
| `reana-s4p-transfer.yaml` | 22 s | reachability probes only |
| `reana-eval-harness.yaml` | 7 m 30 s | DeepEval install |
| `reana-dcache-benchmark.yaml` | 7 m 50 s | DeepEval install, then a fetch |
| `reana.yaml` | 8 m 35 s | DeepEval install, four steps |

A DeepEval install costs most of eight minutes on this cluster. Start
anything that needs one before you begin talking.

## Through the DRP-Hub Run button

The launcher clones the default branch and runs `reana.yaml`, which is the
DeepEval card. The other seven specs are reachable with `reana-client -f` and
invisible to the Hub, because REANA takes one `reana.yaml` per repository.

Two behaviours of the Hub worth knowing before clicking:

- `git_commit` is snapshotted when the card is created and never refreshed,
  so a card made yesterday still pins yesterday's code. Make a new card, or
  patch the field.
- `git_branch` is stored and then ignored. Whatever is on the default branch
  runs, whatever the card says.

## What will not work, and why

**Anything reaching the deployed MCP server from REANA.** Measured twice, by
two different cards: `reana-mcp-observed.yaml` fails the handshake with
`Network is unreachable`, and `reana-s4p-transfer.yaml` reports
`physicsllm-rdm.desy.de` as the one host out of four where TCP fails. The
three dCache doors all answer.

**Anything on compute4punch tonight.** Jobs submit and queue without being
matched to a drone. A DESY-pinned job sat 31 minutes. This is capacity, not
configuration, and an updated `reana_producer_mytoken` was announced at 15:00
on 2026-09-21.

## If a run goes red

`reana-client logs -w <name>` first. Two failures have specific causes worth
recognising:

- `'NoneType' object has no attribute 'splitlines'` is a compute4punch
  submission refusing the image. Only `wlcg-wn:latest` submits there.
- `Invalid placeholder in string` is REANA substituting `${...}` inside a
  step command, so an inline shell loop using `$(...)` or `$i` dies before
  anything runs. Put the shell in a file.

`python3 check_specs.py` catches both before a submission, along with three
more faults that `reana-client validate` passes.
