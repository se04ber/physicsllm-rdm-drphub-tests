# Climbing the DRP-Hub maturity ladder with this card

Criteria read live from `GET /products/{id}/maturity` on 2026-09-20, not from
documentation. Target is **L3, staying private** — L4 requires `visibility:
public`, which is not what a test card should be.

## L2 — four gates

| Gate | This card | Action |
| --- | --- | --- |
| `license` | `LICENSE`, CC-BY-4.0 | set the `license` field to `CC-BY-4.0` on the card |
| `citation_cff_url` | `CITATION.cff` in the repo | point at its raw URL once pushed |
| `release_tag` or pinned `git_commit` | — | tag the repo, e.g. `v0.1.0`, and record the tag or the commit sha |
| `env_image` | `python:3.12-slim` in `reana.yaml` | **pin by digest** and record it |

Pinning the image:

```bash
docker pull python:3.12-slim
docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
# -> python@sha256:<digest>
```

Put that digest in both `reana.yaml` (`environment:`) and the card's
`env_image` field. This is the gate that turns "it ran once" into "it runs the
same way again", so it is worth the two minutes.

## L3 — four gates

| Gate | This card | Action |
| --- | --- | --- |
| executable workflow / entry point | `reana.yaml`, two steps | set `workflow_file: reana.yaml` |
| explicit `validation_scope` | — | see wording below |
| a **passed validation run** | the card must actually run green | submit it and let the callback record the pass |
| provenance binding | — | `last_run_id` is populated by that run |

Suggested `validation_scope`, which should claim only what the card tests:

> Validates that the benchmark bundle conforms to
> `physicsllm.upload_manifest.v1` and that the deterministic metadata gate
> distinguishes faithful predictions from fabricated ones, scored against a
> published SciCat record. Does not validate the metadata-suggestion model
> itself; predictions are fixtures.

Note the third gate: **L3 cannot be reached by filling in fields.** It needs a
real passing run, which is the point of the ladder and the reason this card
exists.

## L4 — deliberately not attempted

Requires a DOI, an archive URL, `visibility: public` and `human_reviewed:
true`. A self-test card should not be public, and publishing is `destructive`
and L4-gated anyway. If this later becomes a citable artifact, the missing
pieces are a Zenodo deposit and a human sign-off — both of which are human
actions by design.

## Where this card would sit

Read from the hub on 2026-09-20, the two Hermes image clones on this account
are at **L1** with `reproducibility_depth: D0`. Reaching L2 puts this card
above them; reaching L3 means it is executable, validated, and provenance-
bound — which is the honest ceiling for something that is private and has no
DOI.

`reproducibility_depth` is a separate D0–D4 enum. This card is deterministic,
dependency-free and needs no network or secrets, so it should not sit at D0.
The exact D-level criteria were not readable from the docs at the time of
writing; ask AIP which level a fully scripted, deterministic, offline card
qualifies for rather than guessing upward.
