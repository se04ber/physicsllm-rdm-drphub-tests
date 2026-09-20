"""Card 1 - validate a benchmark bundle before it is uploaded, and checksum it.

The bundle, not the folder path, is the contract. This checks that a directory
is a well-formed `physicsllm.upload_manifest.v1` bundle and records a checksum
for every declared file, so a run can later say which bytes it used regardless
of whether the bundle came from dCache, a git ref or a share.

Exit code is the gate: 0 means uploadable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

REQUIRED_TOP_LEVEL = ["manifest_version", "upload_id", "workflow", "storage", "dataset", "metrics"]
DETERMINISTIC_GATES = {
    "exit_code_zero",
    "workflow_terminal_status",
    "workflow_audit_paths",
    "forbidden_values_absent",
}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default=".", help="bundle root")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    root = pathlib.Path(args.bundle).resolve()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    notes: list[str] = []

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        problems.append("manifest.json is missing - without it this is a folder, not a bundle")
        _finish(out, problems, notes, {}, None)
        return 1

    manifest = json.loads(manifest_path.read_text())

    for key in REQUIRED_TOP_LEVEL:
        if key not in manifest:
            problems.append(f"manifest is missing required key: {key}")

    if manifest.get("manifest_version") != "physicsllm.upload_manifest.v1":
        problems.append(
            f"unknown manifest_version {manifest.get('manifest_version')!r} - "
            "this validator only knows physicsllm.upload_manifest.v1"
        )

    # Declared files must exist. A manifest pointing at nothing is the most
    # common way an upload arrives broken.
    declared: list[str] = []
    for section in ("dataset", "metrics", "ro_crate", "ground_truth"):
        entry = manifest.get(section)
        if isinstance(entry, dict) and entry.get("path"):
            declared.append(entry["path"])
    for context in manifest.get("contexts", []):
        if context.get("path"):
            declared.append(context["path"])

    checksums: dict[str, str] = {}
    for rel in declared:
        target = root / rel
        if not target.is_file():
            problems.append(f"manifest declares {rel} but it is not in the bundle")
            continue
        checksums[rel] = sha256(target)

    # The tiering rule this whole structure exists to enforce.
    metrics = manifest.get("metrics", {})
    research_only = set(metrics.get("research_only", []))
    required = set(metrics.get("required", []))
    if "deepeval" in required:
        problems.append(
            "deepeval is listed under metrics.required - an LLM judge must never gate an "
            "upload. Move it to research_only."
        )
    if not DETERMINISTIC_GATES.issubset(required):
        missing = sorted(DETERMINISTIC_GATES - required)
        problems.append(f"metrics.required is missing deterministic gates: {', '.join(missing)}")
    if "deepeval" in research_only:
        notes.append("deepeval present as a research_only metric - judged, not gating")

    if manifest.get("guardrails", {}).get("allow_external_network") is not False:
        notes.append("guardrails.allow_external_network is not false - runs may not be reproducible")

    if not manifest.get("ground_truth"):
        notes.append("no ground_truth declared - scoring will be limited to structural gates")

    _finish(out, problems, notes, checksums, manifest)
    return 1 if problems else 0


def _finish(out: pathlib.Path, problems, notes, checksums, manifest) -> None:
    report = {
        "ok": not problems,
        "upload_id": (manifest or {}).get("upload_id"),
        "problems": problems,
        "notes": notes,
        "file_checksums_sha256": checksums,
    }
    (out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if problems:
        print("\nNOT uploadable - fix the problems above.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
