"""Publish a REANA run's results back to S4P over WebDAV.

Append this as the last step of any card. The results were produced in the
workspace, so there is no extra copy and no laptop-to-workspace hop - which is
what made uploading *input* data this way a bad idea and makes publishing
*output* a good one.

Lands them under the layout the project already uses:

    results/experiments/<use_case_id>/<run_id>/

`run_id` defaults to the REANA workflow id, so a result is traceable to the
run that made it without anyone naming anything.

Credential comes from the REANA secret store as S4P_BEARER_TOKEN. Nothing
secret is in this repository.

Idempotent: MKCOL on an existing collection is not an error, and every PUT is
verified by reading the file back.

Large raw data still belongs on rclone or gfal straight to dCache - this is
for run output, not for staging datasets.
"""

from __future__ import annotations

import argparse, base64, datetime, hashlib, json, os, pathlib, ssl, sys
import urllib.error, urllib.request

CTX = ssl.create_default_context()


def dav(method: str, url: str, token: str, data: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=300, context=CTX) as r:
            return r.status, r.read(4096)
    except urllib.error.HTTPError as e:
        return e.code, e.read(2048)


def mkcol_p(base: str, rel: str, token: str, log: list) -> None:
    """Create each path segment in turn; 405 means it already exists."""
    cur = base.rstrip("/")
    for seg in [s for s in rel.strip("/").split("/") if s]:
        cur = f"{cur}/{seg}"
        code, _ = dav("MKCOL", cur + "/", token)
        log.append({"mkcol": cur + "/", "status": code,
                    "note": "already present" if code in (405, 301) else "created" if code in (201,) else "unexpected"})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="results", help="folder of run output to publish")
    ap.add_argument("--base", required=True, help="WebDAV base, e.g. https://host/punch/physicsllm")
    ap.add_argument("--use-case", required=True, help="use_case_id - groups runs of the same task")
    ap.add_argument("--run-id", default="", help="defaults to the REANA workflow id")
    ap.add_argument("--out", default="results")
    ap.add_argument("--max-mb", type=float, default=512.0,
                    help="refuse individual files larger than this - use rclone for those")
    a = ap.parse_args()

    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("S4P_BEARER_TOKEN", "")
    src = pathlib.Path(a.source)

    # Traceability without anyone naming anything: REANA already has an id.
    run_id = (a.run_id or os.environ.get("REANA_WORKFLOW_UUID")
              or os.environ.get("REANA_WORKFLOW_NAME") or "")
    if not run_id:
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("local-%Y%m%dT%H%M%SZ")
    target = f"results/experiments/{a.use_case.strip('/')}/{run_id}"
    a.target = target

    report: dict = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": str(src), "base": a.base, "target": target,
        "use_case_id": a.use_case, "run_id": run_id,
        "run_id_origin": ("--run-id" if a.run_id else
                          "REANA_WORKFLOW_UUID" if os.environ.get("REANA_WORKFLOW_UUID")
                          else "REANA_WORKFLOW_NAME" if os.environ.get("REANA_WORKFLOW_NAME")
                          else "generated locally"),
        "token_supplied": bool(token), "steps": [], "files": [],
    }

    if not token:
        report["verdict"] = "no token"
        report["hint"] = "reana-client secrets-add --env S4P_BEARER_TOKEN=..."
        _finish(out, report); return 0
    if not src.is_dir():
        report["verdict"] = "no results folder to publish"; _finish(out, report); return 0

    files = sorted(p for p in src.rglob("*") if p.is_file())
    too_big = [str(p) for p in files if p.stat().st_size > a.max_mb * 1024 * 1024]
    if too_big:
        report["verdict"] = "refused: file(s) over the size limit for this route"
        report["too_big"] = too_big
        report["hint"] = "large files belong on rclone/gfal straight to dCache, not through a REANA workspace"
        _finish(out, report); return 0

    mkcol_p(a.base, a.target, token, report["steps"])

    ok = True
    for p in files:
        rel = p.relative_to(src).as_posix()
        url = f"{a.base.rstrip('/')}/{a.target.strip('/')}/{rel}"
        parent = str(pathlib.PurePosixPath(rel).parent)
        if parent not in (".", ""):
            mkcol_p(a.base, f"{a.target.strip('/')}/{parent}", token, report["steps"])
        body = p.read_bytes()
        code, _ = dav("PUT", url, token, body)
        # verify by reading back
        vcode, vbody = dav("GET", url, token)
        entry = {
            "file": rel, "bytes": len(body), "put_status": code,
            "verify_status": vcode,
            "sha256": hashlib.sha256(body).hexdigest(),
            "verified": vcode == 200,
        }
        ok = ok and code in (201, 204) and vcode == 200
        report["files"].append(entry)

    report["verdict"] = "uploaded and verified" if ok else "some files failed - see entries"
    report["target_url"] = f"{a.base.rstrip('/')}/{a.target.strip('/')}/"
    _finish(out, report)
    return 0


def _finish(out: pathlib.Path, report: dict) -> None:
    (out / "upload_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, indent=2))
    print(f"files: {len(report.get('files', []))}")


if __name__ == "__main__":
    raise SystemExit(main())
