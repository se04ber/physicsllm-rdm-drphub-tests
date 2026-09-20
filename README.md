# Getting data in and out of S4P (HIFIS / PUNCH dCache at DESY)

Two directions, and they are not symmetric. Read the one you need.

| | Who does it | Where it runs | Automatable |
| --- | --- | --- | --- |
| **In** — your dataset onto S4P | your agent, with your token | your own machine | yes — but not from a card |
| **Out** — a run's results back to S4P | the workflow | wherever REANA put it | yes, one step |

The asymmetry is about *where* the work runs, not about whether it can be
automated. Both directions should be one command. But getting data **in**
uses a credential that is yours, so it runs where you and your credential
are — your own machine, ideally driven by the agent's upload mode. Getting
results **out** happens inside a job that already holds a token and already
has the files, so it costs one line of YAML.

What does **not** work is uploading your data *through a card*: a card runs on
C4P, so you would first have to get the data to the card. That is the only
thing ruled out here.

This card is the tutorial for both, and it runs the checks that prove the
tutorial still describes reality. Canonical copies of this README and of
`s4p-transfer.sh` live in the `physicsllm-rdm` mirror; the card points at them
rather than forking them, so there is one place to fix when an endpoint moves.

---

## Direction 1 — your data onto S4P

### What you need first

Three things. Two are one-off and slow because a person approves them; only
the third is work.

1. **A Helmholtz AAI login.** Through DESY Keycloak if you are at DESY, or
   your own home institution. You almost certainly have this already.
2. **VO membership: `punch` and `physicsllm`.** Both need approval, so
   request them before the day you need them. This is the step that takes
   real time — hours to days, not minutes.
3. **A transfer client on your machine**: `oidc-agent` plus `rclone`.

`preflight.py` in this card tells you which of the three you are missing, by
testing rather than by asking you. Run it first.

### The steps

```bash
git clone <this repo> && cd s4p-transfer
./s4p-transfer.sh setup          # one-off: registers an oidc-agent account
./s4p-transfer.sh check          # proves the token works against your VO path
./s4p-transfer.sh push ./mydata 01Benchmarks/my-dataset
```

That is **three commands after the approvals land**, and the first two are
once per machine, not once per dataset. `setup` will hand you to `oidc-gen`,
which opens a browser for the AAI consent — the one moment the flow needs a
human, by design.

### Why not do this from a card

A card runs on C4P, not on your laptop. To upload your data from a card you
would first have to get your data to the card — which is the problem you were
trying to solve. Routing a large dataset through a REANA workspace adds a full
extra copy, and the workspace is quota-limited besides. Upload direct, once.

### Which endpoint, by size

Checked from outside the DESY network on 2026-09-20, so none of this needs
intranet:

| Your data | Use | Why |
| --- | --- | --- |
| a few files, interactive | `hifis-storage-web.desy.de` in a browser | no client to install |
| a folder, any size | `rclone` via `s4p-transfer.sh push` | resumes, parallelises, verifies |
| already on another grid endpoint | `gfal-copy` third-party copy | never touches your machine |

The WebDAV doors answer from the public internet — `dcache-doma-door01.desy.de`
reports `dCache/10.2.24` with `DAV: 1, 2` and the full method set including
`PUT`, `MKCOL` and `MOVE`. The claim that this needs the intranet does not hold
for these doors; what *is* blocked from outside is our own
`physicsllm-rdm.desy.de:8443`, which is a different service.

---

## Direction 2 — a run's results back onto S4P

Append one step to any card:

```yaml
      - name: publish
        environment: 'python:3.12-slim'
        commands:
          - python publish_results.py --base $S4P_BASE --use-case c5_metadata_gate
```

and give the workflow its credential through the REANA secret store, never
through the repository:

```bash
reana-client secrets-add --env S4P_BEARER_TOKEN=<token>
```

Results land at

```
results/experiments/<use_case_id>/<run_id>/
```

with `run_id` defaulting to the REANA workflow id, so a result is traceable to
the run that produced it without anyone naming anything by hand. Every upload
is verified by reading the file back, and re-running is safe.

Without a token the step reports `no token` and exits 0 — a card that cannot
publish still runs and still produces its results locally. That is deliberate:
the publish step must never be the reason a demo fails.

---

## What `preflight.py` actually checks

It reports, separately, so a failure names its own cause:

- DNS resolution and TCP reach for each door
- an HTTP `OPTIONS`, to read the server banner and the allowed methods
- whether a bearer token is present in the environment
- if one is: a `PROPFIND` on your VO path — which is the only check that can
  tell you your VO membership is *effective* rather than merely granted

It never writes anything and always exits 0. A red line in its output is
information, not a failed job.

## Scope

This card documents and verifies data movement. It does not move your data for
you, and it should not: the credential is yours.
