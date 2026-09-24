# Hello world on a DESY Compute4PUNCH node

The smallest possible job on the `compute4punch` backend, pinned to DESY
drones. One step, standard library only. It writes `outputs/hello.txt` with
the hostname and platform of the node that ran it, so you can see the pin
took effect.

Use it to check that your REANA account can submit to Compute4PUNCH at all,
before trying a card that does real work there.

```
hello_c4p.py   prints a greeting and where it is running
reana.yaml     the card
```

## Before you start

- Access to a REANA instance with the `compute4punch` backend, such as
  `reana-p4n.aip.de`, with `reana-client` configured
  (`REANA_SERVER_URL` and `REANA_ACCESS_TOKEN` set, `reana-client ping`
  answers).
- The `HELMHOLTZ_TOP` secret in your REANA secret store. Compute4PUNCH needs
  it to submit on your behalf. Check with:

```bash
reana-client secrets-list
```

If it is missing, or `compute4punch` fails instantly, the token has expired.
It needs renewing about weekly.

## Run

```bash
reana-client validate -f reana.yaml
reana-client run -f reana.yaml -w c4p-hello
reana-client status -w c4p-hello
```

Then fetch the result:

```bash
reana-client download outputs/hello.txt -w c4p-hello
cat outputs/hello.txt
```

```
hello from compute4punch
time      2026-09-24T14:02:11+00:00
host      <a DESY drone hostname>
platform  Linux-...
python    3.x.y
```

Any environment variable with `TARDIS` or `CONDOR` in its name is listed
below that, which is where the drone identity shows up when the backend
exposes it.

## What to expect

**Queue time.** The DESY pin restricts the job to a smaller pool of drones
and waits for one to fall idle. Pinned jobs have queued fourteen minutes
while an unpinned one finished in three. That is capacity, not a broken
submission. Give it time before assuming something is wrong.

**To run anywhere on Compute4PUNCH instead**, delete the
`c4p_additional_requirements` line. The job then schedules faster, on
whichever site is free.

## Three rules for the C4P backend

Each of these has cost a failed submission, and `reana-client validate`
catches none of them.

- Only `wlcg-wn:latest` has been observed to submit. A Python image, even
  fully qualified, is refused with
  `'NoneType' object has no attribute 'splitlines'`.
- `c4p_cpu_cores` and `c4p_memory_limit` are strings. Quote them.
- No `pip install`. The backend does not let pip write where it needs to,
  so anything that runs there is standard library only.

*Physics-LLM (BMBF ErUM-Data), work package C.5. CC BY 4.0.*
