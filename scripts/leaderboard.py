"""Compare evaluation runs: one row per results/<run>/scores.json.

    python scripts/leaderboard.py          # prints the table, writes results/leaderboard.md

EX is relaxed EX (extra output columns allowed), like every other column.
Percentages are averaged over the engines each run covered.

"Fell for trap" is out of the pairs whose control the model got right, summed
over engines: a model that fails most controls can't fall for many traps, so a
raw count would rank it too well. The note lists what makes a row not directly
comparable with the others (thinking switched off by prefill, a raised token
limit, answers cut off by that limit, answers collected outside the harness).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAX_TOKENS = 2048


def ratio(pairs: list[list[int]]) -> float | None:
    hit, total = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    return 100 * hit / total if total else None


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}%"


def row_for(data: dict) -> dict:
    meta, summ = data["meta"], data["summary"]
    eng = {e: summ[e] for e in meta["engines"]}
    ex = {e: 100 * s["relaxed"] / s["n"] for e, s in eng.items()}
    fell = sum(s["pairs"].get("fell_for_trap", 0) for s in eng.values())
    truncated = sum(s["status"].get("truncated", 0) for s in eng.values())
    notes = []
    if meta["run"].startswith("subagent__"):
        notes.append("answers from Claude Code subagents, not the API")
    if meta.get("no_think_prefill"):
        notes.append("no-think prefill")
    if meta.get("max_tokens") != DEFAULT_MAX_TOKENS and not meta["run"].startswith("subagent__"):
        notes.append(f"max_tokens {meta.get('max_tokens')}")
    if truncated:
        notes.append(f"{truncated} truncated")
    if data["missing"]:
        notes.append(f"incomplete ({data['missing']} missing)")
    return {
        "run": meta["run"],
        "model": meta["model"],
        "sqlite": ex.get("sqlite"),
        "postgres": ex.get("postgres"),
        "control": ratio([s["control"] for s in eng.values()]),
        "trap": ratio([s["trap"] for s in eng.values()]),
        "fell": fell,
        "control_right": fell + sum(s["pairs"].get("both_correct", 0) for s in eng.values()),
        "reproduced": ratio([s["trap_sql_reproduced"] for s in eng.values()]),
        "note": "; ".join(notes),
    }


def render(rows: list[dict]) -> str:
    rows = sorted(rows, key=lambda r: -sum(v for v in (r["sqlite"], r["postgres"]) if v is not None))
    lines = ["| model | EX sqlite | EX postgres | control | trap | gap | fell for trap (of controls right) "
             "| trap_sql reproduced | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        gap = None if r["control"] is None or r["trap"] is None else r["control"] - r["trap"]
        fell = (f"{r['fell']}/{r['control_right']} ({100 * r['fell'] / r['control_right']:.0f}%)"
                if r["control_right"] else "-")
        lines.append(f"| {r['model']} | {fmt(r['sqlite'])} | {fmt(r['postgres'])} | {fmt(r['control'])} | "
                     f"{fmt(r['trap'])} | {'-' if gap is None else f'{gap:.0f} pts'} | {fell} | "
                     f"{fmt(r['reproduced'])} | {r['note']} |")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = [row_for(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted((ROOT / "results").glob("*/scores.json"))]
    if not rows:
        print("no runs in results/; run scripts/evaluate.py first")
        return 1
    table = render(rows)
    print(table)
    (ROOT / "results" / "leaderboard.md").write_text("# Leaderboard\n\n" + table + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
