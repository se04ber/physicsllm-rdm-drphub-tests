# physics-llm RDM: DRP-Hub workflow cards

Four cards, one repository, all on `main`. The DRP-Hub launcher clones the
default branch and nothing else, so a card on any other branch has never been
launchable from the Hub whatever its description said. Keeping them here is the
workaround for that; one repository per card would suit the launcher better.

Every card lives at the repository root with its own spec file. The scripts have
distinct names, so nothing had to move into a subdirectory and no path inside any
spec changed when they were brought together.

| Card | Spec | Backend | Needs a secret? | Status |
|---|---|---|---|---|
| DeepEval over real A_Data | `reana.yaml` | default | no | completed on REANA |
| MCP tool surface, measured | `reana-mcp-observed.yaml` | default | `RDM_MCP_BEARER_TOKEN` | untested with a token |
| PaNOSC search from C4P | `reana-panosc-c4p.yaml` | compute4punch | `RDM_MCP_BEARER_TOKEN`, `HELMHOLTZ_TOP` | never completed |
| S4P transfer | `reana-s4p-transfer.yaml` | default | `S4P_BEARER_TOKEN` | preflight runs, upload needs a token |
| Agentic evaluation harness | `reana-eval-harness.yaml` | default | `EVAL_LLM_API_KEY` | runs; portable, meant for others to run too |

`reana.yaml` is the DeepEval card because it is the only one known to complete
end to end, so the launcher's default lands on something that works. The other
three are launched by naming their spec file.

## Running one

```bash
reana-client run -f reana-mcp-observed.yaml -w mcp-observed
```

From the Hub, the launcher needs to be told which specification to use. If it
cannot yet be told, only `reana.yaml` is reachable, which is the reason for the
choice above.

## Secrets

Nothing secret is committed, and nothing secret can be: the launcher requires the
repository to be anonymously cloneable, so a committed token would be a published
token. Secrets come from the REANA store:

```bash
reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<value>
reana-client secrets-add --env S4P_BEARER_TOKEN=<value>
```

Every card reports a missing secret as a finding and exits 0. A card that is not
set up yet is not a broken card, and a run that fails for want of a token tells
nobody anything they did not already know.

## TLS

All three cards that reach `physicsllm-rdm.desy.de:8443` use `tls_mode: verify`.
Since 2026-09-21 that host serves a certificate issued by GEANT TLS ECC 1,
chaining to the HARICA TLS ECC Root CA 2021 and valid until 2027-04-08. That root
is in the public trust store, so both `python:3.12-slim` and `wlcg-wn:latest`
verify it with no CA bundle added.

`insecure` remains a parameter for the case where the certificate lapses before
anyone renews it. A result obtained that way carries a caveat that a result under
`verify` does not.

## A trap worth knowing about `/health`

`curl -sk https://physicsllm-rdm.desy.de:8443/health` returns 200, and that 200
says nothing about the MCP server. The deployment's Caddy config routes `/mcp*`
to the MCP service and everything else to the chat surface, so `/health` on that
port is answered by the web UI. The way to tell whether the MCP service is up is
to POST to `/mcp`.

## Images

Stock public images only. Every `ghcr.io/se04ber` image is HTTP 403 anonymously,
so REANA on C4P cannot pull one. Until an image is published somewhere public,
`python:3.12-slim` and `wlcg-wn:latest` are the options.

## Per-card detail

* [`docs/mcp-observed.md`](docs/mcp-observed.md): what the MCP card measures,
  what has been verified against the live server, and what has not.

## The evaluation harness

`reana-eval-harness.yaml` is the one card here meant to be run by people other
than us. Point it at your own agentic system and your own OpenAI-compatible
model and it returns correctness, latency and token usage. Nothing in the
measurement path requires our infrastructure.

**A benchmark folder is any directory containing `cases.jsonl`.** Depth does
not matter, so a flat folder and a deep suite tree both work. One file is the
whole requirement: JSON per line with `expected_output`, and `actual_output`
if you ran your system yourself.

**Add `system.json` only if you want the harness to run your system**, which is
what buys latency and token measurements. It declares how to invoke it:

```json
{"kind": "subprocess", "command": ["python3", "my_agent.py"]}
{"kind": "http_endpoint", "base": "https://my-agent.example/answer"}
{"kind": "python_entrypoint", "entrypoint": "my_pkg.agent:answer"}
```

Each receives one case as JSON and returns `{"output": "..."}`.

**Token counting works without our gateway.** A small OpenAI-compatible proxy
ships in the card, runs beside your agent, and records the usage block from
each response. Set `EVAL_LLM_BASE_URL` to your provider and the harness sets
`OPENAI_BASE_URL` for the agent automatically. The proxy forwards your URL
verbatim, because Open WebUI serves `/api/chat/completions` rather than
`/v1/chat/completions` and appending a path is how that becomes a 405.

**Every number says how it was obtained.** `measured` means the harness
observed it, `self_reported` means the case supplied it, `not_available` means
nobody did. A call whose usage block was missing is reported as unmeasured
rather than as zero tokens, because zero would be a lie.

**Judged metrics never gate.** Every agentic metric DeepEval ships requires a
judge model, so a judged score moves when the judge does. Anything that gates
here is computed from what was observed.
