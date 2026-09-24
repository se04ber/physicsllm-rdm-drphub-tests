#!/usr/bin/env python3
"""Catch the card faults REANA's own validator passes.

Every fault here cost a failed submission on 2026-09-21, and every one of them
got four green checks from `reana-client validate` first. The validator checks
the schema; these are the things that are schema-valid and still do not run.

    python3 check_specs.py
"""
from __future__ import annotations

import glob
import pathlib

import yaml

# wlcg-wn:latest is the only image observed to submit on compute4punch.
# Established 2026-09-21 by isolation, three submissions:
#
#   wlcg-wn:latest, no resource fields          submits
#   python:3.12-slim, resource fields present   fails
#   docker.io/library/python:3.12-slim          fails
#
# So it is the image, not the resource fields, and not a missing registry
# prefix. The failure is "'NoneType' object has no attribute 'splitlines'",
# which names neither the image nor the reason, and reana-client validate
# passes the spec with four green checks first.
#
# There is no need to reach for a python image anyway: C4Compute's own
# python_hello/reana-c4p.yaml runs python3 inside wlcg-wn:latest, so that
# image carries Python. If another image is shown to work, add it with a date.
C4P_IMAGES = {"wlcg-wn:latest"}


def check(path: pathlib.Path) -> list[str]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    inputs = spec.get("inputs") or {}
    steps = spec["workflow"]["specification"]["steps"]

    for entry in inputs.get("files") or []:
        if (path.parent / entry).is_dir():
            problems.append(
                f"inputs.files lists {entry!r}, which is a directory. REANA "
                "refuses this at upload; it belongs in inputs.directories.")

    backends = set()
    for step in steps:
        name = step.get("name", "?")
        backend = step.get("compute_backend")
        backends.add(backend)
        if backend == "compute4punch":
            image = str(step.get("environment") or "")
            if image not in C4P_IMAGES:
                problems.append(
                    f"step {name!r} runs on compute4punch with image {image!r}. "
                    f"Only {', '.join(sorted(C4P_IMAGES))} is known to submit "
                    "there; others fail with a NoneType error naming nothing.")
            for field in ("c4p_cpu_cores", "c4p_memory_limit"):
                value = step.get(field)
                if value is not None and not isinstance(value, str):
                    problems.append(
                        f"step {name!r}: {field} is {value!r}; REANA's schema "
                        "requires a string, so quote it.")
            if any("pip install" in c for c in step.get("commands") or []):
                problems.append(
                    f"step {name!r} pip installs on compute4punch, which cannot "
                    "write where pip needs to. Drop the install and rely on the "
                    "stdlib path, or run that step on another backend.")

    if len(backends) > 1 and any(
            "pip install" in c
            for s in steps for c in (s.get("commands") or [])):
        problems.append(
            "steps span more than one compute backend and one of them installs "
            "packages. Installing on one backend and importing on another does "
            "not work; keep them together.")
    return problems


def main() -> int:
    failures = 0
    for name in sorted(glob.glob("**/reana*.yaml", recursive=True)):
        path = pathlib.Path(name)
        problems = check(path)
        mark = "ok  " if not problems else "FAIL"
        print(f"  {mark} {name}")
        for p in problems:
            print(f"         {p}")
        failures += bool(problems)
    print(f"\n{failures} spec(s) with problems"
          if failures else "\nall specs pass the checks REANA's validator does not make")
    # Exit 0 either way: this is a report, and a card that is not ready yet is
    # a finding rather than a broken build.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
