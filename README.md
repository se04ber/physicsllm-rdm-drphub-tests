# physics-llm RDM: DRP-Hub cards

The cards Physics-LLM C.5 publishes on [DRP-Hub](https://drphub-p4n.aip.de),
one folder each. This repository is the overview. Each card is also its own
repository on AIP GitLab, which is what the DRP-Hub Run button clones, so a
card can be launched and can break without touching the others.

| Card | What it is | Launchable repo |
| --- | --- | --- |
| [`drphub-hifis-dcache-token/`](drphub-hifis-dcache-token/) | Step-by-step: get a Helmholtz token, read and write the PUNCH dCache space with `rclone`, mint a read-only credential for REANA. Tutorial only, no code. | [sabrina.ebert/drphub-hifis-dcache-token](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-hifis-dcache-token) |
| [`drphub-survey/`](drphub-survey/) | The ErUM C.4/C.5 community survey. Links to the forms, says how to hand one in. | [sabrina.ebert/drphub-survey](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-survey) |
| [`drphub-agentic-evaluation/`](drphub-agentic-evaluation/) | Evaluate an agent on a test folder: correctness with DeepEval, latency with OpenTelemetry, tokens with a counting proxy. One config.json, one command, locally or as a REANA card, optionally on a folder fetched from dCache. | [sabrina.ebert/drphub-agentic-evaluation](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-agentic-evaluation) |
| [`drphub-c4p-hello/`](drphub-c4p-hello/) | Hello world on a DESY Compute4PUNCH node. One step, standard library only, writes which node ran it. The smallest check that the C4P backend and the DESY pin work for you. | [sabrina.ebert/drphub-c4p-hello](https://gitlab-p4n.aip.de/sabrina.ebert/drphub-c4p-hello) |

Each folder is self-contained. Clone it, or the matching AIP repository,
and start from its README.

## Other cards

One folder each, a `reana.yaml` inside. Run from inside the folder with
`reana-client run -f reana.yaml -w <name>`. Not on DRP-Hub yet.

| Folder | What it is | Backend | Secret |
| --- | --- | --- | --- |
| `deepeval-a-data/` | DeepEval over real A_Data against the published SciCat record | default | none |
| `mcp-selfcontained/` | MCP server and its evaluation in one job, nothing external | default | `RDM_MCP_BEARER_TOKEN` |
| `mcp-observed/` | The nine-tool MCP surface, measured against the deployed server | default | `RDM_MCP_BEARER_TOKEN` |
| `mcp-c4p/` | Same measurement from a DESY drone on compute4punch | compute4punch | `RDM_MCP_BEARER_TOKEN`, `HELMHOLTZ_TOP` |
| `panosc-c4p/` | PaNOSC search over MCP from compute4punch | compute4punch | `RDM_MCP_BEARER_TOKEN`, `HELMHOLTZ_TOP` |
| `s4p-transfer/` | Reachability of the S4P dCache doors, results pushed back | default | `S4P_BEARER_TOKEN` |
| `eval-demo/` | The evaluation harness run against our own benchmark suite, for demos | default | `EVAL_LLM_API_KEY` |

Secrets come from the REANA store, never from the repository:

```bash
reana-client secrets-add --env RDM_MCP_BEARER_TOKEN=<value>
```

A card reports a missing secret as a finding and exits 0, so a run that is
not set up yet is not a broken run. Read the report. Run
`python3 check_specs.py` before submitting, it catches the faults that
`reana-client validate` passes and REANA still refuses.

## Mirroring a folder to its AIP repository

Edit here, then push the folder as that repository's `main`:

```bash
git subtree push --prefix=drphub-agentic-evaluation aip-drphub-agentic-evaluation main
```

Same for the others, swapping the folder and remote name. The remotes are
named `aip-<folder>`.

*Physics-LLM (BMBF ErUM-Data), work package C.5. CC BY 4.0.*
