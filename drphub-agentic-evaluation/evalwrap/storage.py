"""Fetch a folder from dCache over WebDAV, keeping its layout.

Credential is a bearer token, normally a read-only macaroon. Redirects are
refused and only hrefs on the same origin and under the requested tree are
followed, so the token never travels anywhere the caller did not name.
"""
from __future__ import annotations

import os
import pathlib
import posixpath
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

DAV = "{DAV:}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, f"refusing redirect to {newurl}", headers, fp)


def _under(url: str, base: str) -> bool:
    u, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    return (u.scheme, u.netloc) == (b.scheme, b.netloc) and posixpath.normpath(
        urllib.parse.unquote(u.path)).startswith(posixpath.normpath(urllib.parse.unquote(b.path)))


def _request(url: str, method: str, token: str, depth: str, insecure: bool) -> bytes:
    headers = {"Depth": depth, **({"Authorization": f"Bearer {token}"} if token else {})}
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    with opener.open(urllib.request.Request(url, method=method, headers=headers), timeout=120) as r:
        return r.read()


def _walk(base: str, token: str, insecure: bool, errors: list[str]) -> list[str]:
    files, queue, seen = [], [base.rstrip("/") + "/"], set()
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            root = ET.fromstring(_request(url, "PROPFIND", token, "1", insecure))
        except urllib.error.HTTPError as exc:
            errors.append(f"PROPFIND {url}: HTTP {exc.code}")
            continue
        for resp in root.findall(f"{DAV}response"):
            full = urllib.parse.urljoin(url, (resp.findtext(f"{DAV}href") or "").strip())
            if full.rstrip("/") == url.rstrip("/") or not _under(full, base):
                continue
            if resp.find(f".//{DAV}collection") is not None:
                queue.append(full.rstrip("/") + "/")
            else:
                files.append(full)
    return files


def fetch(base: str, out: pathlib.Path, token: str, insecure: bool = False,
          skip: tuple[str, ...] = ("results/",)) -> dict[str, Any]:
    """Download every file under base into out. Returns a report, never raises."""
    report: dict[str, Any] = {"base": base, "token_supplied": bool(token),
                              "tls": "insecure" if insecure else "verified", "files": [], "errors": []}
    if not token:
        report["verdict"] = "no token: set DCACHE_BEARER_TOKEN"
        return report
    prefix = base.rstrip("/").split("://", 1)[1].split("/", 1)[1]
    for href in _walk(base, token, insecure, report["errors"]):
        rel = href.split("://", 1)[1].split("/", 1)[1][len(prefix):].lstrip("/")
        if not rel or rel.startswith(skip):
            continue
        dest = (out / rel).resolve()
        if not str(dest).startswith(str(out.resolve()) + os.sep):
            report["errors"].append(f"{rel}: refused, escapes the target folder")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.write_bytes(_request(href, "GET", token, "0", insecure))
            report["files"].append({"path": rel, "bytes": dest.stat().st_size})
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"{rel}: {type(exc).__name__}: {exc}")
    report["verdict"] = f"{len(report['files'])} file(s) fetched" if report["files"] else "nothing fetched"
    return report
