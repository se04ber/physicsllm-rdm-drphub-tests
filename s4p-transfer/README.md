# Getting data onto S4P (PUNCH storage at DESY)

## 1 — This is the one that works

Use `s4p` from `Infrastructure/c5_infrastructure/HelperScripts`. It is
maintained, it is what we actually run, and this card does not ship a second
uploader beside it.

```bash
S4P=/path/to/HelperScripts
chmod +x "$S4P/s4p"

"$S4P/s4p" setup          # rclone into a local .venv, writes rclone.conf, prints login steps
eval "$(oidc-agent)"      # same shell for everything below
#   then run the oidc-gen line setup prints (an empty password is fine)

"$S4P/s4p" check          # confirms the login and lists your S4P folder
"$S4P/s4p" upload --profile c4-testbeam-raw --dry-run
"$S4P/s4p" upload --profile c4-testbeam-raw
```

`setup` and `oidc-gen` are once per machine, not once per dataset. The browser
consent during `oidc-gen` is the one moment the flow needs a person, by design.

It does more than upload — `setup`, `check`, `login`, `validate`, `ingest`,
`upload`, `prune`. `s4p <command> --help` for each. (The old name
`hifis-upload.sh` still works and forwards here.)

**Prerequisites:** a Helmholtz AAI login, and VO membership in **both** `punch`
and `physicsllm`. Both need approval, so request them well before you need
them — that is the slow step, not the transfer.

---

## (FUTURE — not usable yet)

*(Direct WebDAV upload with an AAI bearer token. Blocked at the identity
provider, not by our tooling: checked against the issuer's own discovery
document on 2026-09-20, `login.helmholtz.de` advertises exactly one storage
scope —* `storage.read:/punch/.*` *— with no `storage.create`, `storage.modify`
or `storage.stage`. A write scope is silently dropped from the grant, so a PUT
is refused however the oidc-agent account was generated. VO membership is not
the problem: a working token carries the* `PUNCH4NFDI`*,* `:physicsllm` *and*
`:alps` *entitlements and its read scope returns* `authorized`*. Until a write
scope exists, writing needs a different credential — a dCache macaroon minted
at the door, X.509/VOMS, or a local dCache account.)*

*(Mocking it meanwhile: the benchmark tree under* `A_Data/physicsllm/` *mirrors
the dCache layout exactly, so cards and scripts run unchanged against a local
copy and will keep working when the write scope arrives.)*

---

## Where things land, and why the layout is worth keeping

```
/pnfs/desy.de/hifis-storage/punch/physicsllm/
    user/<you>/TestBenchmark_<SUITE>/
        datasets/<use_case_id>/<dataset_id>/cases.jsonl
        results/experiments/<use_case_id>/<run_id>/
    01Benchmarks/<upload_id>/
```

Suite names come from `use-case-registry/registry/benchmark_suites.json`
(`alps`, `testbeam`); use-case ids from `registry/use_cases/*.yaml`. No PaN
suite is registered yet, so PETRA III and Tübingen collections need
`--profile custom --dest-rel` until one is added.

### The `cases.jsonl` part is for DeepEval, deliberately

Each dataset carries its own `cases.jsonl`: **one JSON object per line using
DeepEval's own `Golden` field names** — `input`, `expected_output`,
`actual_output`, `context`, `additional_metadata`, `name`, `source_file`. The
file then loads with `Golden(**row)` and no translation layer.

That is the whole reason to prefer those names over invented ones: the dataset
is portable to anyone already using DeepEval, and there is no mapping code to
drift out of date. `additional_metadata` repeats the identifiers the path also
encodes, so a case stays self-describing once lifted out of the tree.

Results go back to `results/experiments/<use_case_id>/<run_id>/`, which is
where `publish_results.py` writes and where the DeepEval card reads — the two
halves agree by construction rather than by convention.

## What this card itself runs

- **`preflight.py`** — tests each prerequisite apart: DNS, TCP, the WebDAV
  method set, whether a token is present, and a `PROPFIND` showing whether VO
  access is *effective* rather than merely granted. A blocked upload then names
  its own cause instead of failing as one opaque 403.
- **`publish_results.py`** — the outbound direction: a finished run's results
  back to `results/experiments/<use_case_id>/<run_id>/`, each file verified by
  reading it back. Reports `no token` and exits 0 when there is no credential.
