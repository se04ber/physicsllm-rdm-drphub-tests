# Getting data onto S4P (HIFIS / PUNCH dCache at DESY)

**Use the HelperScripts. This card does not ship its own uploader.**

The upload tooling already exists and is maintained at
`Infrastructure/c5_infrastructure/HelperScripts/hifis-upload.sh`. An earlier
version of this card carried a second script doing the same job with
different verbs, which is how a project ends up with two half-maintained
uploaders. It has been removed.

## The steps

```bash
HIFIS=/path/to/HelperScripts
chmod +x "$HIFIS/hifis-upload.sh"

"$HIFIS/hifis-upload.sh" setup          # .venv + rclone + rclone.conf + login steps
eval "$(oidc-agent)"                    # same shell for everything below
#   then run the oidc-gen line setup prints (an empty password is fine)

"$HIFIS/hifis-upload.sh" check          # confirms the login, lists your folder
"$HIFIS/hifis-upload.sh" upload --profile c4-testbeam-raw --dry-run
"$HIFIS/hifis-upload.sh" upload --profile c4-testbeam-raw
```

`setup` and `oidc-gen` are once per machine, not once per dataset. The browser
consent during `oidc-gen` is the one moment the flow needs a person, by design.

## What you need first

1. A Helmholtz AAI login — DESY Keycloak, or your home institution.
2. **VO membership in both `punch` and `physicsllm`.** Both need approval, so
   request them before the day you need them. This is the slow step.
3. `oidc-agent` and `rclone` — `setup` installs rclone into a local venv.

## Where it lands

```
/pnfs/desy.de/hifis-storage/punch/physicsllm/
    user/<you>/TestBenchmark_<SUITE>/datasets/<use_case_id>/
    01Benchmarks/<upload_id>/
```

Suite names come from `use-case-registry/registry/benchmark_suites.json`
(`alps`, `testbeam`); use-case ids from `registry/use_cases/*.yaml`. No PaN
suite is registered yet, so PETRA III and Tübingen collections need
`--profile custom --dest-rel` until one is added.

## Upload is currently blocked, and not by you

Checked against the issuer's own discovery document on 2026-09-20,
`login.helmholtz.de` advertises exactly one storage scope:

```
storage.read:/punch/.*
```

No `storage.create`, no `storage.modify`, no `storage.stage`. A write scope is
silently dropped from the grant, so `PUT` is refused however the oidc-agent
account was generated. **This is an issuer limitation, not a setup mistake.**
VO membership is fine: a working token carries the `PUNCH4NFDI`,
`:physicsllm` and `:alps` entitlements and its read scope returns
`authorized`.

Until a write scope exists, writing needs a different credential — a dCache
macaroon minted at the door, X.509/VOMS, or a local dCache account.

## What this card still runs

- **`preflight.py`** — tests each prerequisite apart: DNS, TCP, the WebDAV
  method set, whether a token is present, and a `PROPFIND` showing whether VO
  access is *effective* rather than merely granted. A blocked upload then
  names its own cause instead of failing as one opaque 403.
- **`publish_results.py`** — the other direction: a finished run's results
  back to `results/experiments/<use_case_id>/<run_id>/`, each file verified by
  reading it back. Reports `no token` and exits 0 when there is no credential.
