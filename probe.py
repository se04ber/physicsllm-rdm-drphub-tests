"""Which routes to DESY S4P storage work from this compute node?

Four things are checked independently, because they fail for different
reasons and the difference is the finding:

  dns      can the node resolve the dCache door at all
  tcp      is anything listening, or is a firewall dropping us
  webdav   does an authenticated PROPFIND return a listing
  posix    is the PNFS mount present on whichever node we landed on

A token is read from S4P_BEARER_TOKEN if the REANA secret store supplies one.
Without it the WebDAV leg still reports whether the door answered, which
distinguishes "unreachable" from "reachable but unauthorised" - and that is
the distinction worth bringing back.
"""

from __future__ import annotations

import argparse, datetime, json, os, pathlib, platform, socket, ssl, urllib.error, urllib.request
from urllib.parse import urlparse


def dns_and_tcp(url: str) -> dict:
    host = urlparse(url).hostname or ""
    port = urlparse(url).port or (443 if url.startswith("https") else 80)
    out: dict = {"host": host, "port": port}
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        out["resolves"] = True
        out["addresses"] = sorted({i[4][0] for i in infos})
    except Exception as exc:
        out["resolves"] = False
        out["error"] = repr(exc)
        return out
    try:
        with socket.create_connection((host, port), timeout=10):
            out["tcp_open"] = True
    except Exception as exc:
        out["tcp_open"] = False
        out["tcp_error"] = repr(exc)
    return out


def webdav(url: str, token: str) -> dict:
    body = (
        '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
        "<d:displayname/><d:resourcetype/></d:prop></d:propfind>"
    ).encode()
    headers = {"Depth": "1", "Content-Type": "application/xml"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="PROPFIND")
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
            text = r.read(20000).decode("utf-8", "replace")
            return {"status": r.status, "entries": text.count("<d:response") or text.count("<D:response"),
                    "bytes": len(text)}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "reason": exc.reason,
                "meaning": "reachable but not authorised" if exc.code in (401, 403) else "reachable, error"}
    except Exception as exc:
        return {"status": None, "error": repr(exc), "meaning": "not reachable"}


def posix(root: str, rel: str) -> dict:
    p = pathlib.Path(root)
    out = {"root": root, "mounted": p.is_dir()}
    if out["mounted"]:
        target = p / rel
        out["probe_path_exists"] = target.is_dir()
        try:
            out["entries_visible"] = len(list(target.iterdir())[:25]) if target.is_dir() else 0
        except Exception as exc:
            out["listing_error"] = repr(exc)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--webdav", required=True)
    ap.add_argument("--posix", required=True)
    ap.add_argument("--path", default="01Benchmarks")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("S4P_BEARER_TOKEN", "")
    url = f"{a.webdav.rstrip('/')}/{a.path.strip('/')}/"

    report = {
        "checked_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ran_on": {"fqdn": socket.getfqdn(), "arch": platform.machine(),
                   "python": platform.python_version()},
        "token_supplied": bool(token),
        "network": dns_and_tcp(a.webdav),
        "webdav": webdav(url, token),
        "posix": posix(a.posix, a.path),
        "url_probed": url,
    }

    net, dav, pos = report["network"], report["webdav"], report["posix"]
    works = [n for n, ok in (
        ("webdav", dav.get("status") == 207),
        ("posix", bool(pos.get("probe_path_exists"))),
    ) if ok]
    report["routes_that_work"] = works
    report["verdict"] = (
        "no route from this node" if not works else f"reachable via {', '.join(works)}"
    )
    if not net.get("resolves"):
        report["diagnosis"] = "DNS does not resolve the dCache door from this node"
    elif not net.get("tcp_open"):
        report["diagnosis"] = "DNS resolves but TCP is blocked - firewall between this node and DESY"
    elif dav.get("status") in (401, 403):
        report["diagnosis"] = "door reachable; credential missing or rejected"

    (out / "access_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0   # the finding is the deliverable, either way


if __name__ == "__main__":
    raise SystemExit(main())
