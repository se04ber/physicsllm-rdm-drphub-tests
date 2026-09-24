# physics-llm RDM: DRP-Hub cards

The cards Physics-LLM C.5 publishes on [DRP-Hub](https://drphub-p4n.aip.de),
one folder each. This repository is the overview. The cards on the Hub are
also their own repositories on AIP GitLab, which is what the Run button
clones, so one card can be launched, or break, without touching the others.

## On DRP-Hub

| Folder | What it is | Repository |
| --- | --- | --- |
| [`dcache-token/`](dcache-token/) | Step by step: get a Helmholtz token, read and write the PUNCH dCache space with `rclone`, mint a read-only credential for REANA. Tutorial only. | [drphub-hifis-dcache-token](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-hifis-dcache-token) |
| [`survey/`](survey/) | The ErUM C.4/C.5 community survey: where the forms are, how to hand one in. | [drphub-survey](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-survey) |
| [`agent-evaluation/`](agent-evaluation/) | Evaluate an agent on a test folder: correctness with DeepEval, latency with OpenTelemetry, tokens with a counting proxy. One `config.json`, one command. | [drphub-agentic-evaluation](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-agentic-evaluation) |
| [`c4p-hello/`](c4p-hello/) | Hello world on a DESY Compute4PUNCH node. The smallest check that the backend and the DESY pin work for you. | [drphub-c4p-hello](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-c4p-hello) |

Each folder is self-contained. Clone it, or its repository, and start from
its README.

## Not on DRP-Hub yet

Run from inside the folder with `reana-client run -f reana.yaml -w <name>`.

| Folder | What it is | Backend | Secrets |
| --- | --- | --- | --- |
| [`mcp-tools/`](mcp-tools/) | 24 questions to the RDM MCP server through its nine tools, scored and timed. `reana.yaml` brings the server along, `reana-desy.yaml` reaches the deployed one from a DESY node. | default, or C4P | `RDM_MCP_BEARER_TOKEN` |
| [`metadata-vs-scicat/`](metadata-vs-scicat/) | DeepEval over real P08 reflectivity data: our extracted metadata against the published SciCat record. | default | none |
| [`s4p-transfer/`](s4p-transfer/) | Reachability of the S4P dCache doors, layer by layer, and pushing a run's results back. | default | `S4P_BEARER_TOKEN` |
| [`eval-demo/`](eval-demo/) | The evaluation run against our own benchmark suite, as shown at the Potsdam workshop. | default | `EVAL_LLM_API_KEY` |

Secrets come from the REANA store, never from a repository:

```bash
reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<value>
```

A card reports a missing secret as a finding and exits 0, so a run that is
not set up yet is not a broken run. Read the report. Before submitting,
`python3 check_specs.py` catches the faults `reana-client validate` passes
and REANA still refuses.

## Mirroring a folder to its repository

Edit here, then push the folder as that repository's `main`:

```bash
git subtree push --prefix=agent-evaluation aip-drphub-agentic-evaluation main
```

Same for the others. Remotes are named `aip-<repository>`.

*Physics-LLM (BMBF ErUM-Data), work package C.5. CC BY 4.0.*
