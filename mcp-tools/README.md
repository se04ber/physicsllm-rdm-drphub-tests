# Checking the RDM MCP server

Asks the server 24 questions through its nine read-only tools, scores the
answers, and times every call. The expected answers are fixed by files the
server itself reads, so a failed case means the deployment changed.

```
run.py                asks, scores, times
mcp_client.py         a standard-library MCP client with a span per call
cases/cases.jsonl     the 24 questions, each with its tool, arguments and expected answer
reana.yaml            runs anywhere: the server travels with the card
reana-desy.yaml       runs against the deployed server, from a DESY C4P node
serve_and_measure.sh  starts the server on loopback for reana.yaml
```

## Run it anywhere

The card brings the server with it, so this needs no access to DESY.

```bash
reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<value>
reana-client run -f reana.yaml -w mcp-check
```

About 20 seconds. The same token authenticates the server and the client.

## Run it against the deployed server

From a DESY Compute4PUNCH node, since the server is not reachable from
REANA's default backend.

```bash
reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<value>
reana-client run -f reana-desy.yaml -w mcp-desy
```

Needs `HELMHOLTZ_TOP` in the secret store as well. Pinned jobs can queue for
a while, that is capacity, not a failure.

## Locally

```bash
pip install deepeval==4.2.3 opentelemetry-sdk opentelemetry-api
export RDM_MCP_BEARER_TOKEN=<value>
python3 run.py --base https://physicsllm-rdm.desy.de:8443
```

Both libraries are optional. Without DeepEval the same comparisons run in
plain Python and each case records which path scored it. Without
OpenTelemetry latencies still come from a timer.

## Reading the output

```
mcp-tools   http://127.0.0.1:8000/mcp   tls=verify
  session   established
  tools     matches the nine documented read-only tools
  deepeval  on   opentelemetry on   spans 27

  ok   resolve_technique:xrr_phrase_uri                    2.1 ms
  ok   find_schema_for:xrr_schema_id                       1.8 ms
  --   resolve_technique:out_of_vocabulary                 2.4 ms
  ...
  23/24 scored across 6 tools, 0 failed, 27 calls in 206 ms
  gate      pass
```

`--` is a case that was not scored. One case is reference-free by design:
its phrase is in no pinned vocabulary, so the tool falls through to PaNET's
live endpoint, whose answer the card does not control. Without a token
every case shows `--` and a report is still written.

A refusal can be the right answer. `describe_schema` on an unknown id is
expected to return a tool error, because inventing a schema is the failure
that tool exists to prevent.

`results/report.json` has every case with its call, `results/otel_summary.json`
has per-tool call counts and latencies. Cases that ask about `panosc_search`
and `panosc_get_dataset` are deliberately absent: those reach a third-party
index whose results change, so nothing can be pinned to them.

## Two things to know

`curl https://physicsllm-rdm.desy.de:8443/health` answers 200 from the chat
UI, not from the MCP server. The only way to tell whether the MCP service is
up is to POST to `/mcp`, which is what this card does.

`tls_mode` is `verify`. The host has served a GEANT certificate since
2026-09-21. `insecure` still works if it lapses, and a result obtained that
way carries a caveat.

*Physics-LLM (BMBF ErUM-Data), work package C.5. CC BY 4.0.*
