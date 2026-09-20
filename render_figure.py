"""Render the self-test result as an SVG, using only the standard library.

A card whose only output is JSON is hard to read at a glance. This draws the
two arms field by field so the discrimination is visible immediately.

No matplotlib, no pip install: the card stays dependency-free, which is what
lets it run unchanged on any image.
"""

from __future__ import annotations

import json
import pathlib
import xml.sax.saxutils as xml

W, ROW_H, TOP, LEFT, COL = 1120, 34, 150, 40, 470

CSS = """
.bg{fill:#fbfbf9}
.t{font:700 25px system-ui,-apple-system,'Segoe UI',sans-serif;fill:#111110}
.st{font:14px system-ui,-apple-system,'Segoe UI',sans-serif;fill:#55534e}
.h{font:800 11px system-ui,-apple-system,'Segoe UI',sans-serif;fill:#8d8a83;letter-spacing:.07em}
.f{font:600 13px ui-monospace,monospace;fill:#111110}
.v{font:12px ui-monospace,monospace;fill:#55534e}
.ok{font:700 13px system-ui,sans-serif;fill:#1a7f5a}
.no{font:700 13px system-ui,sans-serif;fill:#b3321f}
.note{font:12px system-ui,-apple-system,'Segoe UI',sans-serif;fill:#55534e}
.rule{stroke:#dcdad2;stroke-width:1}
.hard{stroke:#111110;stroke-width:1.4}
"""


def esc(value: object, limit: int = 52) -> str:
    text = "-" if value is None else str(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return xml.escape(text)


def render(summary: dict, out: pathlib.Path) -> None:
    faithful, fabricated = summary["arms"]
    failed = {row["field"]: row for row in fabricated["failures"]}

    # Field order comes from the faithful arm's evaluation, so the figure
    # always matches what actually ran.
    detail = json.loads((out / "faithful" / "evaluation.json").read_text())
    fields = [row["field"] for row in detail["gate"]["cases"]]

    height = TOP + ROW_H * len(fields) + 150
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'font-family="system-ui, sans-serif"><style>{CSS}</style>',
        f'<rect class="bg" width="{W}" height="{height}"/>',
        f'<text class="t" x="{LEFT}" y="44">Does the gate catch a fabricated value?</text>',
        f'<text class="st" x="{LEFT}" y="70">Same bundle, same six cases, scored twice. '
        f'Ground truth is a published SciCat record, used unmodified.</text>',
        f'<text class="st" x="{LEFT}" y="90">{esc(summary["ground_truth"], 150)}</text>',
        f'<line class="hard" x1="{LEFT}" y1="110" x2="{W - LEFT}" y2="110"/>',
        f'<text class="h" x="{LEFT}" y="132">FIELD</text>',
        f'<text class="h" x="{LEFT + COL}" y="132">FAITHFUL ARM</text>',
        f'<text class="h" x="{LEFT + COL + 250}" y="132">FABRICATED ARM</text>',
        f'<line class="rule" x1="{LEFT}" y1="140" x2="{W - LEFT}" y2="140"/>',
    ]

    for index, field in enumerate(fields):
        y = TOP + ROW_H * index + 22
        parts.append(f'<text class="f" x="{LEFT}" y="{y}">{esc(field, 34)}</text>')
        parts.append(f'<text class="ok" x="{LEFT + COL}" y="{y}">✓ match</text>')
        if field in failed:
            row = failed[field]
            parts.append(f'<text class="no" x="{LEFT + COL + 250}" y="{y}">✗ {esc(row["predicted"], 40)}</text>')
        else:
            parts.append(f'<text class="ok" x="{LEFT + COL + 250}" y="{y}">✓ match</text>')
        parts.append(
            f'<line class="rule" x1="{LEFT}" y1="{y + 11}" x2="{W - LEFT}" y2="{y + 11}"/>'
        )

    base = TOP + ROW_H * len(fields)
    parts += [
        f'<text class="f" x="{LEFT}" y="{base + 36}">verdict</text>',
        f'<text class="ok" x="{LEFT + COL}" y="{base + 36}">'
        f'PASS &#183; {faithful["passed"]}/{faithful["total"]} &#183; exit {faithful["exit_code"]}</text>',
        f'<text class="no" x="{LEFT + COL + 250}" y="{base + 36}">'
        f'FAIL &#183; {fabricated["passed"]}/{fabricated["total"]} &#183; exit {fabricated["exit_code"]}</text>',
        f'<line class="hard" x1="{LEFT}" y1="{base + 52}" x2="{W - LEFT}" y2="{base + 52}"/>',
        f'<text class="note" x="{LEFT}" y="{base + 78}">'
        f'{esc("run_description is absent from the published record, so unknown is the correct answer. "
              "The fabricated arm invented a plausible value and was caught.", 200)}</text>',
        f'<text class="note" x="{LEFT}" y="{base + 98}">'
        f'No judge model was involved in either verdict: DeepEval is declared research_only and '
        f'reported skipped in both arms.</text>',
        "</svg>",
    ]

    (out / "figure.svg").write_text("\n".join(parts) + "\n")


if __name__ == "__main__":
    results = pathlib.Path("results")
    render(json.loads((results / "summary.json").read_text()), results)
    print("wrote results/figure.svg")
