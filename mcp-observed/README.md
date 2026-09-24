# A live MCP server, scored with DeepEval and measured with OpenTelemetry

This card asks a running MCP server questions whose answers are fixed by
files the server itself reads, checks what comes back, and times every call
it makes. A regression in the deployment shows up here as a failed case,
which is the thing a stored artefact scored offline can never tell you.

The server is the RDM MCP service at `https://physicsllm-rdm.desy.de:8443`.
It speaks MCP Streamable HTTP at `/mcp` and exposes nine read-only tools.

## What the card does

| step | what happens |
| --- | --- |
| `install` | `deepeval==4.2.3` and the OpenTelemetry SDK, both tolerant of failure |
| `measure` | handshake, `tools/list`, then one `tools/call` per case, scored and timed |

Three files come out, listed under `outputs.files` in `reana.yaml`:

* `results/mcp_card_report.json` is the whole run: the session, the tool
  surface, every case with its latency, and the verdict.
* `results/otel_summary.json` is per-tool call count, mean and standard
  deviation of latency, total response bytes, and total wall time.
* `results/otel_spans.json` is the spans themselves, as exported.

With `--write-results` the per-dataset metrics also land in the tree, at
`<suite>/results/experiments/<use_case_id>/<run_id>/`, alongside a
`config_run.json` and a copy of the spans under `traces/`.

## The tool surface, asked rather than assumed

The card calls `tools/list` and compares the answer with the nine tools the
server is documented to expose:

```
panosc_search   panosc_get_dataset   resolve_technique
find_schema_for   list_schemas   describe_schema
validate_draft    list_mappings  describe_mapping
```

That list is read from `services/mcp/src/rdm_mcp/server.py` in the
physics-llm-rdm repository, where every one of the nine carries
`read_only_hint=true`. The report says which tools were advertised, which
documented ones were missing, which undocumented ones appeared, and which
advertised tool failed to carry `readOnlyHint`.

This check exists because the claim has been wrong before. A `system.json`
on this repository's `deepeval-a-data` branch names `rdm_route_request`,
`panet_search`, `catalog_search` and `catalog_get_record` as tools of this
endpoint. None of those four is on it. That file has not been corrected on
its own branch; the `system.json` shipped with this card carries the real
nine and says so.

## The cases

Twenty-four cases, in the layout the project already uses, which is the
layout the data has in dCache:

```
<suite>/datasets/<use_case_id>/<dataset_id>/cases.jsonl
<suite>/use_cases/<use_case_id>/system.json
<suite>/results/experiments/<use_case_id>/<run_id>/     <- written by the run
```

`cases.jsonl` is one JSON object per line using DeepEval's own `Golden`
field names, so a row loads with `Golden(**row)` and there is no translation
layer to drift out of date. What this card adds sits in
`additional_metadata`: which tool answers the case, with which arguments,
and which part of the response is the answer.

| tool | cases | example |
| --- | --- | --- |
| `resolve_technique` | 7 | `"XRR"` resolves to `PaNET01149` |
| `find_schema_for` | 7 | x-ray reflectivity is covered by schema `Reflectivity` |
| `describe_schema` | 5 | `alps` is a draft 2020-12 object schema, and an unknown id is refused |
| `describe_mapping` | 2 | `reflectivity_nexus_v1` targets `Reflectivity` |
| `list_schemas` | 2 | the answer names the registry document it came from |
| `list_mappings` | 1 | the same, for the mapping registry |

Every expected value is traced to the file in the physics-llm-rdm context
release that fixes it, named per case in `source_file` and explained in
`additional_metadata.golden_source`. The goldens come from
`panet_technique_vocabulary.json`, `technique_schema_crosswalk.json`,
`c5_rdm_schema_registry_catalog.json`, `c5_rdm_mapping_registry_index.json`
and the ALPS EasyLog reference.

One case is marked reference-free and gates nothing. The phrase
`"trombone lubrication rate"` is in no pinned vocabulary, so the tool falls
through to PaNET's live SPARQL endpoint, whose answer this card does not
control. The response is recorded and no golden is invented for it.

`build_cases.py` regenerates `cases.jsonl` and carries the provenance of
each golden in readable form. It is shipped so the dataset can be rebuilt
after the context release changes.

Three tools are deliberately not exercised. `panosc_search` and
`panosc_get_dataset` reach a third-party discovery API whose result set
changes, so nothing here can be pinned to it. `validate_draft` needs
`describe_schema` to succeed first, and that path has not yet been confirmed
green against the deployment.

## Metrics

Three DeepEval metrics, all of which consult no model:

| metric | used for |
| --- | --- |
| `ExactMatchMetric` | a value the context release fixes exactly |
| `PatternMatchMetric` | a value whose shape is fixed, such as a PaNET URI |
| `JsonCorrectnessMetric` | a response that must carry a named set of fields |

There is no judged tier. This card has no model endpoint, so a judged metric
would have nothing to run against, and a tier that can never run is worse
than an absent one.

`JsonCorrectnessMetric` in deepeval 4.2.3 asks for a model object when it is
constructed, even though its scoring is `pydantic.model_validate_json` and
reaches nothing. The card hands it one that raises if it is ever called, and
sets `include_reason=False`, which is the only path that would have reached
a model. If that ever changes the card fails loudly instead of quietly
acquiring a judge.

The install step cannot fail the run. When `deepeval` is missing, the same
comparison is computed in plain Python and every case records which path
produced its number, under `computed_by`.

## The gate has three values

`pass`, `fail` and `not-scored`, kept from `eval_benchmark.py` on the
`deepeval-a-data` branch. A case with no `actual_output` was never answered,
so it is not scored, and it counts as neither a pass nor a failure. Without
a token every case is in that state and the card still writes its report.

Two states that look alike are kept apart:

* The call never happened, or the transport failed. `actual_output` stays
  empty, the error is recorded, and the case is not scored.
* The call succeeded and the answer lacked the field the case asks about.
  `actual_output` becomes the JSON literal `null`, the case is scored, and
  it fails. The server answered, and the answer was wrong.

A refusal can itself be the right answer. `describe_schema` on an
unregistered id is expected to return a tool error, because fabricating a
schema is the failure that tool exists to avoid. That case scores the
outcome word.

## Telemetry

Every call gets one span named `execute_tool`, matching the convention in
`services/api/src/c5/services/rdm/observability/instrument.py` so a single
query reads both sides of the system. Spans carry
`gen_ai.operation.name = execute_tool`, and `gen_ai.tool.name` for a
`tools/call`. The GenAI semantic conventions are pinned at 1.38.0 in one
constant, because every `gen_ai.*` attribute is still Development status.

`gen_ai.usage.*` is unset everywhere on purpose. These calls reach an MCP
server and no model. There are no prompt or completion tokens, and a zero
would be a measurement claim about something that never happened.

The exporter is in-memory, because the job has no collector to send to. The
spans are read back at the end of the run and written to a file.

Latencies do not depend on the SDK. `mcp_client.py` records the wall-clock
latency, the tool name, the success flag and the response size for every
call in plain Python, so the summary is produced even when the
OpenTelemetry install failed. That was tested by running the card with
neither wheel installed.

## The token

The card needs `RDM_MCP_BEARER_TOKEN` in the environment. It must come from
the REANA secret store:

```bash
reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<the secret>
```

Nothing secret is committed anywhere in this repository, and nothing may be.
The DRP-Hub launcher clones the default branch anonymously, so a committed
token would be a published token. Without one the run reports "no token",
marks every case not-scored, and exits 0.

## TLS

`tls_mode` defaults to `verify`. Since 2026-09-21 the host serves a
certificate issued by GEANT TLS ECC 1, chaining to the HARICA TLS ECC Root CA
2021, valid for `physicsllm-rdm.desy.de` until 2027-04-08. That root is in the
public trust store, so a REANA job verifies it with nothing added: the
`python:3.12-slim` image already carries the CA bundle.

`insecure` remains available for the case where the certificate lapses before
anyone renews it. Use it knowing what it costs: the card then accepts any
certificate the host presents, so a network attacker in the path could
impersonate the server, and the bearer token travels over that connection.
A result obtained that way carries a caveat that a result under `verify`
does not.

### A trap worth knowing about `/health`

`curl -sk https://physicsllm-rdm.desy.de:8443/health` returns 200, and that
200 says nothing about the MCP server. The deployment's Caddy config routes
`/mcp*` to the MCP service and everything else to the nanobot chat surface,
so `/health` on that port is answered by the web UI. The MCP server's own
health route is behind the same prefix rule. The way to tell whether the MCP
service is up is to POST to `/mcp`, which is what this card does.

## What has been verified, and what has not

Verified against the live server on 2026-09-21:

* `POST /mcp` with no credential returns HTTP 401 `{"error":"unauthorized"}`.
  The card reports that as a failed handshake, marks every case not-scored,
  writes all three output files and exits 0.
* The same with a credential the server rejects. One `initialize` span is
  produced, with `rdm.mcp.http_status = 401` and status ERROR, and the
  latency appears in the summary.
* The certificate verifies against the public trust store. `curl` without
  `-k` reports `ssl_verify_result=0`, so `tls_mode: verify` is the default.

Verified against a local harness that imports the deployed server's own
`rdm_mcp.rdm_registry` and `physics_llm.schema_discovery` modules and wraps
them in the same JSON-RPC envelope:

* All 23 scored cases pass. The 24th is the reference-free one.
* The full protocol path works: `initialize`, `notifications/initialized`,
  the `Mcp-Session-Id` header, `tools/list` and `tools/call`.
* The nine-tool surface check passes, including `readOnlyHint` on all nine.
* The run works with deepeval and OpenTelemetry present, and with neither.
* Both tree shapes work: a root holding several suites, and a single suite
  directory of the kind an S4P mount would give.

**Not yet verified: an authenticated call against the deployed server.** No
bearer token for that deployment was available while this card was written,
and the runbook records that `reana-client secrets-add` has not been run for
it either. So the transport, the scoring, the gate and the telemetry are all
exercised, and the one thing still untested is whether the deployment's own
context release agrees with the goldens. If it has drifted, the affected
cases will fail and name the difference, which is the card working.

## Running it locally

```bash
pip install deepeval==4.2.3 opentelemetry-sdk opentelemetry-api
export RDM_MCP_BEARER_TOKEN=...        # never commit this
python run_card.py --tree benchmark/TestBenchmark_PaN \
                   --base https://physicsllm-rdm.desy.de:8443 \
                   --tls verify --out results --write-results
```

It exits 0 when the token is absent, when the server is unreachable, when
the server refuses the credential, and when either install failed. A missing
credential is not a broken card. An unexpected failure is still allowed to
fail the step, because a card that swallows those tells nobody anything.
