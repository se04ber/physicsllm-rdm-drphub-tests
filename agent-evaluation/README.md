# Evaluating an agent

Point it at a folder, get correctness, latency and token usage back.
Correctness comes from [DeepEval](https://github.com/confident-ai/deepeval),
latency from an OpenTelemetry span around each call, tokens from a small
proxy that sits between your agent and its model. Same numbers on your
laptop and on REANA.

## 1. Clone, copy the example

```bash
git clone https://gitlab-p4n.aip.de/sabrina.ebert/drphub-agentic-evaluation.git
cd drphub-agentic-evaluation
cp -r example testFolder1
```

A test folder looks like this:

```
testFolder1/
  config.json          all settings
  dataset/cases.jsonl  the questions and expected answers
  agentCode/agent.py   your agent, optional
  results/             written by the run
```

## 2. Edit config.json

```json
{
  "path": ".",
  "storage": "local",
  "agent": "agentCode/agent.py",
  "repeats": 1,
  "metrics": { "correctness": true, "latency": true, "tokens": true },
  "model": { "base_url": "https://api.helmholtz-blablador.fz-juelich.de/v1", "name": "alias-fast" }
}
```

| Key | Meaning |
| --- | --- |
| `path` | `"."` for this folder, or a dCache URL when `storage` is `"dcache"` |
| `storage` | `"local"` or `"dcache"` |
| `agent` | script to run per case, or `null` to score answers already in `cases.jsonl` |
| `repeats` | runs per case, 3 or more to see a spread in latency |
| `metrics` | switch each one off with `false` |
| `model` | where your agent's model is. The key goes in the environment, never here |

**Your dataset.** One JSON object per line in `dataset/cases.jsonl`:

```json
{"name": "xrr_acronym", "input": "Three-letter acronym for X-ray reflectivity?", "expected_output": "XRR"}
```

Add `actual_output` if you already ran your system and only want the
answers graded. Subfolders with their own `cases.jsonl` become separate
rows in the result.

**Your agent.** Any script that reads one case as JSON on stdin and prints
`{"output": "..."}`. `example/agentCode/agent.py` is a complete one to
copy. It calls its model through `OPENAI_BASE_URL`, which the wrapper sets
to its own counting proxy, so token counting needs nothing from your code.

## 3. Run

```bash
pip install deepeval==4.2.3 opentelemetry-sdk opentelemetry-api
export EVAL_LLM_API_KEY=<key>
python3 run.py testFolder1/
```

```
  DATASET   CASES  PASS  GATE   LATENCY
  dataset       3     3  pass   412 ± 38 ms

  tokens   129 over 3 model calls (measured)
  gate     1 pass
```

`results/report.json` has every case, every error and how each number was
obtained: `measured`, `self_reported` or `not_available`. A number that
could not be measured is never written as zero. `results/summary.txt` is
the table above.

A failing gate is often the right answer. `PETRA III` against `Petra III`
passes after normalisation, `metre` against `seconds` is a real difference.

## On REANA

Put your test folder in this repository, add it under `inputs.directories`
in `reana.yaml`, then:

```bash
reana-client secrets-add --env EVAL_LLM_API_KEY=<key>
reana-client run -f reana.yaml -w my-eval -p test_folder=testFolder1
```

The DeepEval install takes most of eight minutes. Results come back in
`results/`.

## From dCache

Set `"storage": "dcache"` and `"path"` to the folder on dCache that holds
`dataset/` and `agentCode/`. The wrapper fetches it into the test folder
first. Needs `DCACHE_BEARER_TOKEN` in the environment, or in the REANA
secret store. How to get one is in
[drphub-hifis-dcache-token](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-hifis-dcache-token),
which also covers uploading your folder with `rclone`.

*Physics-LLM (BMBF ErUM-Data), work package C.5. CC BY 4.0.*
