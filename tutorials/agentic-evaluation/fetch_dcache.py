#!/usr/bin/env python3
"""Fetch a benchmark tree from dCache over WebDAV, preserving its layout.

The evaluation runners read a directory tree, and dCache speaks WebDAV, so
something has to bridge the two. This walks a remote prefix with PROPFIND and
GETs every file into the same relative structure, which means an uploaded
bundle is evaluated exactly as it was laid out rather than after a translation
step nobody can audit.

Credential is a dCache macaroon in DCACHE_BEARER_TOKEN, from the REANA secret
store. A macaroon is the right shape here: it carries a path caveat and an
expiry, so the credential itself is bounded to the tree it is meant to read.

Stdlib only, and always exits 0. Nothing fetched is a finding, not a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

DAV = "{DAV:}"


def _request(url: str, method: str, token: str, depth: str = "1",
             insecure: bool = False) -> bytes:
    headers = {"Depth": depth}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers)
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        return r.read()


def walk(base: str, token: str, insecure: bool) -> list[str]:
    """Every file href under base, depth-first. Directories end with a slash."""
    found: list[str] = []
    queue = [base.rstrip("/") + "/"]
    seen = set()
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            body = _request(url, "PROPFIND", token, "1", insecure)
        except urllib.error.HTTPError as exc:
            print(f"  PROPFIND {url} -> HTTP {exc.code}", file=sys.stderr)
            continue
        root = ET.fromstring(body)
        for resp in root.findall(f"{DAV}response"):
            href = (resp.findtext(f"{DAV}href") or "").strip()
            if not href or href.rstrip("/") == url.rstrip("/").split("://", 1)[-1].split("/", 1)[-1]:
                pass
            full = href if href.startswith("http") else (
                url.split("/", 3)[0] + "//" + url.split("/", 3)[2] + href)
            if full.rstrip("/") == url.rstrip("/"):
                continue
            iscoll = resp.find(f".//{DAV}collection") is not None
            (queue if iscoll else found).append(full if iscoll else full)
            if iscoll and not full.endswith("/"):
                queue[-1] = full + "/"
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True,
                    help="WebDAV URL of the tree root, e.g. "
                         "https://dcache-doma-door01.desy.de/punch/physicsllm/01Benchmarks")
    ap.add_argument("--out", default="benchmark")
    ap.add_argument("--insecure", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("DCACHE_BEARER_TOKEN", "")
    out = pathlib.Path(a.out)
    report = {"base": a.base, "token_supplied": bool(token), "files": [], "errors": []}

    if not token:
        report["verdict"] = ("no token: set DCACHE_BEARER_TOKEN in the REANA secret "
                             "store with a dCache macaroon scoped to this path")
        print(report["verdict"])
    else:
        prefix = a.base.rstrip("/").split("://", 1)[1].split("/", 1)[1]
        for href in walk(a.base, token, a.insecure):
            rel = href.split("://", 1)[1].split("/", 1)[1]
            rel = rel[len(prefix):].lstrip("/")
            if not rel:
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_bytes(_request(href, "GET", token, "0", a.insecure))
                report["files"].append({"path": str(dest), "bytes": dest.stat().st_size})
                print(f"  fetched {rel} ({dest.stat().st_size} bytes)")
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{rel}: {type(exc).__name__}: {exc}")
        report["verdict"] = (f"{len(report['files'])} file(s) fetched"
                             if report["files"] else "nothing fetched")
        print(f"\n{report['verdict']}")

    pathlib.Path("results").mkdir(exist_ok=True)
    pathlib.Path("results/fetch_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
