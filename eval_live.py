"""Score the live router over MCP, not a stored artefact.

The rest of this card compares a metadata draft that was produced earlier.
This half asks the running system to make a decision now, and checks it -
so a regression in the deployed router shows up as a failed case rather
than as a stale file nobody re-generated.

The cases are not invented. Every routable intent in
`physics_llm.routing.CAPABILITY_OFFERS` ships an example phrasing under
`say`, written to illustrate that intent. Those phrases are the inputs and
the intent they are filed under is the expected answer. If the router
cannot route its own documented examples, that is worth knowing.

Two things are scored. `rdm_route_request` answers "did the router send
this to the right place". `panet_search` answers "does the technique
vocabulary resolve the words a scientist actually types" - which is the
concrete example for C.2 and C.3: the SPARQL graph stays behind the tool,
and the caller asks a question instead of writing a query.

Reads cases from the benchmark tree when one is mounted:

    datasets/<use_case_id>/cases.jsonl

and falls back to the bundled copy otherwise, so the card runs with or
without S4P.

Needs RDM_MCP_BEARER_TOKEN. Without it the whole step reports "no token"
and exits 0 - a card must not fail because a credential is absent.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://physicsllm-rdm.desy.de:8443"


def context(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def rpc(base: str, method: str, params: dict, token: str, rid: int,
        ctx: ssl.SSLContext) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/mcp", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=90, context=ctx) as handle:
            raw = handle.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code,
                "error": exc.read().decode("utf-8", "replace")[:200]}
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    text = raw
    if "\ndata:" in raw or raw.lstrip().startswith("event:"):
        for line in raw.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    try:
        return {"ok": True, "body": json.loads(text)}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON reply: {text[:160]}"}


def load_cases(data_dir: pathlib.Path, use_case: str) -> tuple[list[dict], str]:
    """Prefer the benchmark tree; fall back to the copy that ships here."""
    mounted = data_dir / "datasets" / use_case / "cases.jsonl"
    bundled = data_dir / "routing_cases.jsonl"
    path = mounted if mounted.is_file() else bundled
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return cases, str(path)


def unwrap(body: dict) -> dict:
    """MCP wraps tool output in content blocks; get back to the object."""
    result = body.get("result") or {}
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                return json.loads(block.get("text") or "{}")
            except json.JSONDecodeError:
                continue
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.environ.get("MCP_BASE", DEFAULT_BASE))
    ap.add_argument("--data", default="data")
    ap.add_argument("--use-case", default="c5_metadata_suggestion")
    ap.add_argument("--out", default="results")
    ap.add_argument("--tls", choices=("verify", "insecure"), default="verify")
    a = ap.parse_args()

    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    token = (os.environ.get("RDM_MCP_BEARER_TOKEN") or "").strip()
    cases, source = load_cases(pathlib.Path(a.data), a.use_case)

    report: dict = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base": a.base,
        "cases_from": source,
        "case_count": len(cases),
        "token_supplied": bool(token),
        "results": [],
    }

    if not token:
        report["verdict"] = "no token - set RDM_MCP_BEARER_TOKEN in the REANA secret store"
        _finish(out, report)
        return 0

    ctx = context(a.tls == "insecure")
    init = rpc(a.base, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "deepeval-live", "version": "1"},
    }, token, 1, ctx)
    if not init.get("ok"):
        report["verdict"] = f"MCP handshake failed: {init.get('error')}"
        _finish(out, report)
        return 0

    # ---- PaNET term resolution ------------------------------------------
    # The concrete example for C.2 / C.3: a technique vocabulary reached as
    # a tool. The SPARQL endpoint stays behind it - the caller asks the
    # question, not the graph. `raw_sparql_allowed: false` in our own
    # translation config is the same decision stated from the other side.
    panet_path = pathlib.Path(a.data) / "panet_cases.jsonl"
    panet_cases = ([json.loads(x) for x in panet_path.read_text().splitlines() if x.strip()]
                   if panet_path.is_file() else [])
    panet_pass = 0
    rid = 1000
    for case in panet_cases:
        rid += 1
        call = rpc(a.base, "tools/call",
                   {"name": "panet_search", "arguments": {"query": case["query"]}},
                   token, rid, ctx)
        entry: dict = {"query": case["query"], "kind": case["kind"],
                       "expected_uri": case["expected_uri"]}
        if not call.get("ok"):
            entry.update(ok=False, error=call.get("error"))
            report.setdefault("panet", []).append(entry)
            continue
        answer = unwrap(call["body"])
        hits = answer.get("results") or answer.get("matches") or answer.get("concepts") or []
        uris = [h.get("uri") for h in hits if isinstance(h, dict)]
        top = uris[0] if uris else None
        entry.update(ok=True, top_uri=top, hit_count=len(uris))
        # An out-of-vocabulary term must return nothing. Inventing a URI is
        # the failure this case exists to catch.
        entry["pass"] = (top == case["expected_uri"]) if case["expected_uri"] else not uris
        panet_pass += entry["pass"]
        report.setdefault("panet", []).append(entry)
    if panet_cases:
        report["panet_verdict"] = f"{panet_pass}/{len(panet_cases)} terms resolved as pinned"

    passed = 0
    for index, case in enumerate(cases, start=2):
        want = case.get("expected_intent")
        call = rpc(a.base, "tools/call",
                   {"name": "rdm_route_request",
                    "arguments": {"request": case["request"]}},
                   token, index, ctx)
        entry: dict = {"request": case["request"], "expected_intent": want}
        if not call.get("ok"):
            entry.update(ok=False, error=call.get("error"))
            report["results"].append(entry)
            continue

        answer = unwrap(call["body"])
        got = answer.get("intent") or (answer.get("clarify") or {}).get("intent")
        routed = bool(answer.get("routed"))
        # Two things are scored apart: did it route at all, and did it route
        # to the right place. A refusal to guess is not the same failure as
        # a confident wrong answer.
        entry.update(ok=True, routed=routed, intent=got,
                     intent_match=(got == want),
                     expected_routed=case.get("expected_routed", True))
        entry["pass"] = bool(entry["intent_match"]
                             and routed == entry["expected_routed"])
        passed += entry["pass"]
        report["results"].append(entry)

    report["passed"] = passed
    report["verdict"] = f"{passed}/{len(cases)} routed as documented"
    _finish(out, report)
    return 0


def _finish(out: pathlib.Path, report: dict) -> None:
    (out / "live_routing.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Live router over MCP  {report['utc']}")
    print(f"cases: {report['case_count']} from {report['cases_from']}\n")
    for entry in report.get("panet", []):
        if not entry.get("ok"):
            print(f"  ERR  panet {entry['query'][:24]:<26} {str(entry.get('error'))[:52]}")
            continue
        mark = "ok  " if entry.get("pass") else "FAIL"
        got = entry.get("top_uri") or "no match"
        print(f"  {mark} panet {entry['query'][:24]:<26} {str(got).rsplit('/', 1)[-1]}")
    if report.get("panet_verdict"):
        print(f"  -> {report['panet_verdict']}\n")
    for entry in report.get("results", []):
        if not entry.get("ok"):
            print(f"  ERR  {entry['request'][:52]:<54} {str(entry.get('error'))[:60]}")
            continue
        mark = "ok  " if entry.get("pass") else "FAIL"
        print(f"  {mark} {entry['request'][:52]:<54} "
              f"routed={str(entry.get('routed')):<5} intent={entry.get('intent')}")
    print(f"\nverdict: {report['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
