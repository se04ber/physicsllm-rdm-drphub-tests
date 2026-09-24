#!/usr/bin/env python3
"""Evaluate an agent on a test folder.

    python3 run.py testFolder1/

The folder holds config.json, dataset/, optionally agentCode/, and receives
results/. Everything is set in config.json. The only thing outside it is the
model API key, read from the environment variable EVAL_LLM_API_KEY, so a
credential never sits in a file that gets copied around.

Always exits 0. A folder that is not set up yet is a finding in the report,
not a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from evalwrap import measure, storage  # noqa: E402

DEFAULTS = {"path": ".", "storage": "local", "agent": None, "repeats": 1,
            "metrics": {"correctness": True, "latency": True, "tokens": True}, "model": {}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="test folder containing config.json")
    ap.add_argument("--out", help="where to write results (default: <folder>/results)")
    a = ap.parse_args()

    folder = pathlib.Path(a.folder).resolve()
    cfg_path = folder / "config.json"
    if not cfg_path.is_file():
        print(f"no config.json in {folder}")
        return 0
    cfg = {**DEFAULTS, **json.loads(cfg_path.read_text(encoding="utf-8"))}
    out = pathlib.Path(a.out).resolve() if a.out else folder / "results"

    if cfg["storage"] == "dcache":
        print(f"\n  fetching {cfg['path']}")
        report = storage.fetch(cfg["path"], folder, os.environ.get("DCACHE_BEARER_TOKEN", ""),
                               insecure=bool(cfg.get("insecure")))
        out.mkdir(parents=True, exist_ok=True)
        (out / "fetch_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  {report['verdict']}")
        for err in report["errors"][:5]:
            print(f"  {err}")
        if cfg.get("agent"):
            print("  note: the agent was fetched from shared storage and will be executed")
    elif cfg["path"] not in (".", ""):
        folder = (folder / cfg["path"]).resolve()

    measure.evaluate(folder, cfg, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
