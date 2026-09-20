"""Reach the DESY RDM MCP server from a Compute4PUNCH job, and search over it.

The point of this card is a reachability claim, so it is written to be
falsifiable: it records which drone it landed on, then tests each layer
separately - DNS, TCP, the MCP handshake, the tool list, and finally a real
PaNOSC search. A failure at any layer names that layer rather than collapsing
into "could not connect".

Correcting an earlier claim: `physicsllm-rdm.desy.de:8443` is NOT intranet-only.
Measured 2026-09-20 from a home network, it answers HTTP 200 - what fails is
certificate verification, because the host presents a self-signed certificate
(subject == issuer, CN=physicsllm-rdm.desy.de). `curl -w %{http_code}` reports
000 for a TLS failure exactly as it does for no route, which is how the two got
confused. Pinning to a DESY drone is therefore about provenance and policy,
not about reachability.

Standard library only - the same job showed that pip cannot write to HOME on
this backend, so a card that needs an install is a card that does not run.

Always exits 0. A negative result is the finding.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import socket
import ssl
import sys
import urllib.error
import urllib.request

JSONRPC = "2.0"


def tls_probe(hostname: str, port: int) -> dict:
    """Look at the certificate before deciding anything about the connection.

    Measured 2026-09-20: this host answers 200 from the public internet but
    presents a SELF-SIGNED certificate, so every verifying client fails with
    CERTIFICATE_VERIFY_FAILED. Read as "no route" that is simply wrong - which
    is the mistake this function exists to prevent.
    """
    entry: dict = {}
    try:
        with socket.create_connection((hostname, port), timeout=15) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) as tls:
                entry.update(ok=True, verified=True, peer=tls.getpeercert().get("subject"))
                return entry
    except ssl.SSLCertVerificationError as exc:
        entry.update(ok=True, verified=False, verify_error=str(exc)[:160])
    except OSError as exc:
        return {"ok": False, "verified": False, "error": f"{type(exc).__name__}: {exc}"}

    # Unverified handshake, purely to report WHAT the certificate is.
    lax = ssl.create_default_context()
    lax.check_hostname = False
    lax.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((hostname, port), timeout=15) as raw:
            with lax.wrap_socket(raw, server_hostname=hostname) as tls:
                der = tls.getpeercert(binary_form=True)
                entry["cert_sha256"] = hashlib.sha256(der or b"").hexdigest()
    except OSError as exc:
        entry["cert_error"] = str(exc)
    return entry


def build_ctx(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


CTX = ssl.create_default_context()


def condor_env() -> dict[str, str]:
    """Which drone ran this. Without it the reachability claim is unverifiable."""
    keys = ("_CONDOR_SLOT", "_CONDOR_JOB_AD", "_CONDOR_MACHINE_AD", "CONDOR_JOB_ID",
            "TardisDroneUuid", "HOSTNAME", "SITE_NAME")
    found = {k: os.environ[k] for k in keys if os.environ.get(k)}
    found["resolved_hostname"] = socket.gethostname()
    try:
        found["resolved_fqdn"] = socket.getfqdn()
    except OSError:
        pass
    return found


def rpc(base: str, method: str, params: dict | None, token: str, rid: int) -> dict:
    """One JSON-RPC call over MCP Streamable HTTP."""
    body = json.dumps({"jsonrpc": JSONRPC, "id": rid, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/mcp", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as handle:
            raw = handle.read().decode("utf-8", "replace")
            status = handle.status
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code,
                "error": exc.read().decode("utf-8", "replace")[:300]}
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # Streamable HTTP may answer as SSE; take the first data: line if so.
    text = raw
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        for line in raw.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    try:
        return {"ok": True, "status": status, "body": json.loads(text)}
    except json.JSONDecodeError:
        return {"ok": False, "status": status, "error": f"non-JSON reply: {text[:200]}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.environ.get("MCP_BASE", "https://physicsllm-rdm.desy.de:8443"))
    ap.add_argument("--query", default="reflectivity")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--out", default="results")
    ap.add_argument("--insecure", action="store_true",
                    help="proceed past a certificate that does not verify - the server "
                         "currently presents a self-signed one; recorded in the report")
    a = ap.parse_args()

    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("RDM_MCP_BEARER_TOKEN", "")
    host = a.base.split("://", 1)[-1].split("/")[0]
    hostname, _, port = host.partition(":")
    port = int(port or 443)

    report: dict = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base": a.base,
        "ran_on": condor_env(),
        "token_supplied": bool(token),
        "layers": {},
    }

    # 1 - DNS
    try:
        addrs = sorted({i[4][0] for i in socket.getaddrinfo(hostname, None)})
        report["layers"]["dns"] = {"ok": True, "addresses": addrs}
    except OSError as exc:
        report["layers"]["dns"] = {"ok": False, "error": str(exc)}

    # 2 - TCP. This is the layer the public internet fails at.
    if report["layers"]["dns"].get("ok"):
        try:
            with socket.create_connection((hostname, port), timeout=15):
                report["layers"]["tcp"] = {"ok": True}
        except OSError as exc:
            report["layers"]["tcp"] = {"ok": False, "error": str(exc)}

    # TLS before MCP: a self-signed certificate is not a routing failure, and
    # reporting it as one sent us down the wrong path once already.
    if report["layers"].get("tcp", {}).get("ok"):
        report["layers"]["tls"] = tls_probe(hostname, port)
        if report["layers"]["tls"].get("ok") and not report["layers"]["tls"].get("verified"):
            print("NOTE: the certificate does not verify (self-signed). "
                  + ("continuing because --insecure was given.\n" if a.insecure
                     else "re-run with --insecure to look past it.\n"))

    global CTX
    CTX = build_ctx(a.insecure)
    tls_usable = report["layers"].get("tls", {}).get("verified") or a.insecure
    reachable = report["layers"].get("tcp", {}).get("ok") and tls_usable
    if reachable:
        # 3 - MCP handshake
        init = rpc(a.base, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "drphub-c4p-card", "version": "1"},
        }, token, 1)
        report["layers"]["mcp_initialize"] = init

        if init.get("ok"):
            # 4 - what the server offers
            listed = rpc(a.base, "tools/list", {}, token, 2)
            names = []
            if listed.get("ok"):
                names = [t.get("name") for t in
                         (listed["body"].get("result", {}).get("tools") or [])]
            report["layers"]["tools_list"] = {"ok": listed.get("ok"), "tools": names,
                                              "error": listed.get("error")}

            # 5 - a real search, preferring the PaNOSC-facing tool
            tool = next((n for n in ("panet_search", "catalog_search") if n in names), None)
            if tool:
                called = rpc(a.base, "tools/call",
                             {"name": tool, "arguments": {"query": a.query, "limit": a.limit}},
                             token, 3)
                report["layers"]["search"] = {"ok": called.get("ok"), "tool": tool,
                                              "error": called.get("error")}
                if called.get("ok"):
                    (out / "search_result.json").write_text(
                        json.dumps(called["body"], indent=2) + "\n")
            else:
                report["layers"]["search"] = {
                    "ok": False,
                    "error": "neither panet_search nor catalog_search was offered"}

    ok = lambda k: report["layers"].get(k, {}).get("ok")  # noqa: E731
    if not report["layers"].get("dns", {}).get("ok"):
        report["verdict"] = "DNS did not resolve - not a routing question"
    elif not report["layers"].get("tcp", {}).get("ok"):
        report["verdict"] = "no TCP route to the MCP server - it is down, or this job has no path to it"
    elif not tls_usable:
        report["verdict"] = ("reachable, but the certificate does not verify (self-signed). "
                             "This is a certificate problem, NOT a network problem - "
                             "re-run with --insecure to test the layers above it")
    elif not ok("mcp_initialize"):
        report["verdict"] = "reachable, but the MCP handshake failed - check the bearer token"
    elif not ok("search"):
        report["verdict"] = "MCP is up and listed its tools, but the search call failed"
    else:
        report["verdict"] = "reached the MCP server from C4P and completed a search"

    (out / "mcp_probe.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"MCP over Compute4PUNCH  {report['utc']}")
    print(f"base: {a.base}")
    print(f"ran on: {report['ran_on'].get('resolved_fqdn') or report['ran_on'].get('resolved_hostname')}\n")
    for layer in ("dns", "tcp", "tls", "mcp_initialize", "tools_list", "search"):
        entry = report["layers"].get(layer)
        if entry is None:
            print(f"  {layer:<16} not attempted")
            continue
        mark = "ok  " if entry.get("ok") else "FAIL"
        extra = entry.get("error") or (", ".join(entry.get("tools") or []) if layer == "tools_list" else "")
        print(f"  {mark} {layer:<16} {str(extra)[:90]}")
    print(f"\nverdict: {report['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
