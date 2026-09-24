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

MINT IT AT THE DOOR ROOT. The URL you POST the request to becomes a path
caveat of its own, in addition to any you ask for, and the pair permits that
exact path and nothing below it:

    POST https://door/punch/physicsllm/user/me/Benchmarks/   ->  403 on any subdirectory
    POST https://door/                                       ->  207 on the whole subtree

Measured 2026-09-21. The symptom is a 403 that looks like a permission
problem on the data, which is where an evening goes. Read caveats back with:

    python3 -c "import base64,sys; t=sys.argv[1]; print(base64.urlsafe_b64decode(t+'='*(-len(t)%4)).decode('utf-8','replace'))" "$MACAROON"

Two identical path caveats means it was minted at the wrong URL.

Stdlib only, and always exits 0. Nothing fetched is a finding, not a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import posixpath
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DAV = "{DAV:}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects outright.

    A redirect is chosen by the server, and urllib replays the Authorization
    header to wherever it points. With a bearer credential in that header, an
    open or hostile redirect hands the macaroon to a third party. Nothing in
    this fetch needs redirects, so the safe behaviour is to have none.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing redirect to {newurl}: the credential would travel with it",
            headers, fp)


def _same_origin(url: str, base: str) -> bool:
    """True when url is on base's origin and under base's path.

    PROPFIND hrefs come from the server, so they are untrusted input. Without
    this check a crafted response steers the next request, and the bearer
    token, anywhere it likes.
    """
    u, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    if (u.scheme, u.netloc) != (b.scheme, b.netloc):
        return False
    return posixpath.normpath(urllib.parse.unquote(u.path)).startswith(
        posixpath.normpath(urllib.parse.unquote(b.path)))


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
    opener = urllib.request.build_opener(_NoRedirect,
                                         urllib.request.HTTPSHandler(context=ctx))
    with opener.open(req, timeout=120) as r:
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
            full = urllib.parse.urljoin(url, href)
            if full.rstrip("/") == url.rstrip("/"):
                continue
            # The href came from the server. Anything off this origin, or
            # outside the tree we were asked to read, is not followed and the
            # credential never reaches it.
            if not _same_origin(full, base):
                print(f"  skipped off-tree href: {full}", file=sys.stderr)
                continue
            iscoll = resp.find(f".//{DAV}collection") is not None
            if iscoll:
                queue.append(full if full.endswith("/") else full + "/")
            else:
                found.append(full)
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
    report = {"base": a.base, "token_supplied": bool(token),
              "tls": "insecure" if a.insecure else "verified",
              "files": [], "errors": []}
    if a.insecure:
        # Recorded in the report as well as printed: a result obtained without
        # certificate verification carries a caveat, and a reader of the JSON
        # should not have to have seen the console to know that.
        print("  WARNING: --insecure disables certificate verification, and the "
              "bearer token travels over that connection. The dCache doors have "
              "presented valid certificates since 2026-09-21, so this should not "
              "be needed.", file=sys.stderr)

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
            # rel is derived from a server-supplied href, so it is untrusted.
            # Without this a response containing ../ writes outside --out.
            dest = (out / rel).resolve()
            if not str(dest).startswith(str(out.resolve()) + os.sep):
                report["errors"].append(f"{rel}: refused, escapes --out")
                print(f"  refused (escapes --out): {rel}", file=sys.stderr)
                continue
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
