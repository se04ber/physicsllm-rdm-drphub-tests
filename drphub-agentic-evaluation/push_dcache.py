#!/usr/bin/env python3
"""Upload a benchmark folder to dCache, after checking it can be evaluated.

Why this exists alongside the rclone helper. An upload over WebDAV with a
macaroon needs nothing installed: no rclone, no oidc-agent, no per-user
config, no intranet. The credential carries its own path and activity
caveats, so it is bounded by construction rather than by the uploader
behaving.

And it checks before it writes. A bundle that reaches the store and then
turns out to have no cases.jsonl, or a malformed one, costs a round trip
through a REANA run to discover. Validation here is cheap and local.

    DCACHE_BEARER_TOKEN=<macaroon> python3 push_dcache.py \
        --source ./with_agent \
        --dest https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks/my_group/with_agent

Always exits 0: a bundle that is not ready is a finding, not a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib replays Authorization to wherever they point."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing redirect to {newurl}: the credential would travel with it",
            headers, fp)


def _send(url: str, method: str, token: str, body: bytes | None = None,
          insecure: bool = False) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/octet-stream"
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with opener.open(req, timeout=300) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def validate(source: pathlib.Path) -> tuple[list[str], list[str]]:
    """What the runner will need, checked before anything is written."""
    problems: list[str] = []
    notes: list[str] = []

    cases = sorted(source.rglob("cases.jsonl"))
    if not cases:
        problems.append("no cases.jsonl anywhere under the source; nothing here "
                        "can be evaluated")
    for path in cases:
        rows, bad = 0, 0
        graded = 0
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                if bad == 1:
                    problems.append(f"{path.name}: line {n} is not valid JSON")
                continue
            if not isinstance(row, dict):
                problems.append(f"{path.name}: line {n} is not a JSON object")
                continue
            if (row.get("expected_output") or "").strip():
                graded += 1
        rel = path.relative_to(source)
        if rows == 0:
            problems.append(f"{rel}: empty")
        elif graded == 0:
            # Not fatal. A reference-free bundle is legitimate; it simply
            # cannot be scored against goldens, and the uploader should know
            # that now rather than read it in a report later.
            notes.append(f"{rel}: {rows} case(s), none with expected_output, "
                         "so nothing here will be scored against a golden")
        else:
            notes.append(f"{rel}: {rows} case(s), {graded} with a golden label")

        system = path.parent / "system.json"
        if system.is_file():
            try:
                kind = json.loads(system.read_text(encoding="utf-8")).get("kind")
                notes.append(f"{rel.parent}/system.json: kind={kind}")
            except ValueError:
                problems.append(f"{rel.parent}/system.json is not valid JSON")
    return problems, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--dest", required=True, help="WebDAV URL to write under")
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    source = pathlib.Path(a.source).resolve()
    # Any bearer dCache accepts. Despite the name, this is usually NOT a
    # macaroon for an upload: macaroons are read-only at this door, issued
    # with activity:UPLOAD and then refused at write time. Writes use the
    # user's own OIDC token:
    #     DCACHE_BEARER_TOKEN="$(oidc-token HIFIS)" python3 push_dcache.py ...
    token = os.environ.get("DCACHE_BEARER_TOKEN", "")
    dest = a.dest.rstrip("/")

    problems, notes = validate(source)
    for n in notes:
        print(f"  {n}")
    sys.stdout.flush()   # or stderr below jumps ahead of the notes
    for p in problems:
        print(f"  PROBLEM  {p}", file=sys.stderr)
    sys.stderr.flush()
    if problems:
        print("\nnot uploading: fix the above first", file=sys.stderr)
        return 0
    if not token:
        print("\nno token: set DCACHE_BEARER_TOKEN. For an upload this is your "
              "own OIDC token, not a macaroon:\n"
              '  DCACHE_BEARER_TOKEN="$(oidc-token HIFIS)"', file=sys.stderr)
        return 0

    files = sorted(p for p in source.rglob("*") if p.is_file())
    print(f"\n{len(files)} file(s) to {dest}")
    sent = failed = 0
    made: set[str] = set()
    for f in files:
        rel = f.relative_to(source).as_posix()
        # Create parents first: a PUT to a path whose collection does not
        # exist is refused, and MKCOL on an existing one is harmless.
        parts = rel.split("/")[:-1]
        for i in range(len(parts)):
            d = "/".join(parts[: i + 1])
            if d not in made:
                _send(f"{dest}/{urllib.parse.quote(d)}", "MKCOL", token,
                      insecure=a.insecure)
                made.add(d)
        url = f"{dest}/{urllib.parse.quote(rel)}"
        if a.dry_run:
            print(f"  would put {rel} ({f.stat().st_size} bytes)")
            continue
        code = _send(url, "PUT", token, f.read_bytes(), a.insecure)
        if 200 <= code < 300:
            sent += 1
            print(f"  put {rel} ({f.stat().st_size} bytes)")
        else:
            failed += 1
            print(f"  FAILED {rel}: HTTP {code}", file=sys.stderr)
    print(f"\n{sent} uploaded, {failed} failed" if not a.dry_run
          else f"\ndry run: {len(files)} file(s) would be uploaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
