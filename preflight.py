"""Check what a person actually needs before they can upload to S4P.

Answers the question by testing rather than by asking, and reports each layer
separately so a failure names its own cause: a DNS failure, a blocked port, a
rejected token and an unapproved VO membership look identical from the outside
unless you check them apart.

Always exits 0. A red line here is information, not a failed job - the card
should still run and still publish its report when the answer is "you are not
set up yet".

Standard library only, so it runs on the stock image with nothing installed.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import socket
import ssl
import sys
import urllib.error
import urllib.request

# Checked reachable from outside the DESY network on 2026-09-20. The last
# entry is ours and is expected to fail from outside - it is in the list as a
# control, so a run that fails everything can be told apart from one where
# only the intranet-only service is unreachable.
DOORS = [
    ("dcache-doma-door01.desy.de", 443, "https", "primary WebDAV door"),
    ("dcache-desy-webdav.desy.de", 2880, "https", "second WebDAV door"),
    ("hifis-storage-web.desy.de", 443, "https", "browser interface"),
    ("physicsllm-rdm.desy.de", 8443, "https", "our own service (intranet-only: expected to fail here)"),
]

CTX = ssl.create_default_context()
TIMEOUT = 8


def resolve(host: str) -> dict:
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return {"ok": True, "addresses": addrs}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def tcp(host: str, port: int) -> dict:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def options(url: str) -> dict:
    """OPTIONS reads the banner and the method set without touching any data."""
    req = urllib.request.Request(url, method="OPTIONS")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            headers = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        status = exc.code
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "status": status,
        "server": headers.get("server", ""),
        "dav": headers.get("dav", ""),
        "allow": headers.get("allow", ""),
    }


def propfind(url: str, token: str) -> dict:
    """The only check that can tell you your VO membership is effective."""
    req = urllib.request.Request(url, method="PROPFIND")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Depth", "0")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            status = r.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    meaning = {
        207: "VO membership is effective - you can upload",
        401: "token rejected: expired, or issued by an issuer the door does not trust",
        403: "authenticated but not authorised - this is the VO approval step",
        404: "path does not exist - check S4P_BASE",
    }.get(status, "unexpected - ask the dCache operators")
    return {"ok": True, "status": status, "meaning": meaning, "can_upload": status == 207}


def main() -> int:
    out = pathlib.Path("results")
    out.mkdir(parents=True, exist_ok=True)

    base = os.environ.get("S4P_BASE", "https://dcache-doma-door01.desy.de/punch/physicsllm")
    token = os.environ.get("S4P_BEARER_TOKEN", "")

    report: dict = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base": base,
        "doors": [],
        "token": {},
        "still_needed": [],
    }

    any_reachable = False
    for host, port, scheme, note in DOORS:
        entry: dict = {"host": host, "port": port, "note": note}
        entry["dns"] = resolve(host)
        if entry["dns"]["ok"]:
            entry["tcp"] = tcp(host, port)
            if entry["tcp"]["ok"]:
                entry["options"] = options(f"{scheme}://{host}:{port}/")
                if not host.startswith("physicsllm-rdm"):
                    any_reachable = True
        report["doors"].append(entry)

    if not any_reachable:
        report["still_needed"].append(
            "network: no S4P door was reachable from here. If this ran on REANA, "
            "the job may have no outbound network - which is a cluster policy "
            "question for the site, not a problem with your account."
        )

    if not token:
        report["token"] = {"supplied": False}
        report["still_needed"].append(
            "a token: run ./s4p-transfer.sh setup on your own machine, or for a "
            "workflow, reana-client secrets-add --env S4P_BEARER_TOKEN=..."
        )
    else:
        result = propfind(base.rstrip("/") + "/", token)
        report["token"] = {"supplied": True, "propfind": result}
        if result.get("ok") and not result.get("can_upload"):
            report["still_needed"].append(f"VO access: {result['meaning']}")
        elif not result.get("ok"):
            report["still_needed"].append(f"VO access: could not be tested ({result.get('error')})")

    report["ready_to_upload"] = not report["still_needed"]

    (out / "preflight.json").write_text(json.dumps(report, indent=2) + "\n")

    # Human-readable summary - this is what someone reads in the REANA log.
    print(f"S4P preflight  {report['utc']}")
    print(f"base: {base}\n")
    for d in report["doors"]:
        opts = d.get("options", {})
        state = (
            "unresolved" if not d["dns"]["ok"]
            else "no route" if not d.get("tcp", {}).get("ok")
            else f"HTTP {opts.get('status', '?')} {opts.get('server', '')}".strip()
        )
        print(f"  {d['host']}:{d['port']:<5} {state}   ({d['note']})")
    print()
    if report["ready_to_upload"]:
        print("ready: everything this card can check is in place.")
    else:
        print("still needed:")
        for item in report["still_needed"]:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
