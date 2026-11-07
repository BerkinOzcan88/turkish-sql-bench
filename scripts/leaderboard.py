"""Compare evaluation runs: one row per results/<run>/scores.json.

    python scripts/leaderboard.py          # prints the table, writes results/leaderboard.md

Percentages are averaged over the engines each run covered. "Fell for trap"
counts pairs where the control was right and the trap wrong, summed over engines.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ratio(pairs: list[list[int]]) -> float | None:
    hit, total = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    return 100 * hit / total if total else None


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}%"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    for path in sorted((ROOT / "results").glob("*/scores.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta, summ = data["meta"], data["summary"]
        eng = {e: summ[e] for e in meta["engines"]}
        ex = {e: 100 * s["ex"] / s["n"] for e, s in eng.items()}
        rows.append({
            "run": meta["run"],
            "model": meta["model"],
            "sqlite": ex.get("sqlite"),
            "postgres": ex.get("postgres"),
            "control": ratio([s["control"] for s in eng.values()]),
            "trap": ratio([s["trap"] for s in eng.values()]),
            "fell": sum(s["pairs"].get("fell_for_trap", 0) for s in eng.values()),
            "pairs": sum(sum(s["pairs"].values()) for s in eng.values()),
            "reproduced": ratio([s["trap_sql_reproduced"] for s in eng.values()]),
            "missing": data["missing"],
        })
    if not rows:
        print("no runs in results/; run scripts/evaluate.py first")
        return 1
    rows.sort(key=lambda r: -sum(v for v in (r["sqlite"], r["postgres"]) if v is not None))
    lines = ["| model | EX sqlite | EX postgres | control | trap | gap | fell for trap | trap_sql reproduced | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        gap = None if r["control"] is None or r["trap"] is None else r["control"] - r["trap"]
        note = f"incomplete ({r['missing']} missing)" if r["missing"] else ""
        lines.append(f"| {r['model']} | {fmt(r['sqlite'])} | {fmt(r['postgres'])} | {fmt(r['control'])} | "
                     f"{fmt(r['trap'])} | {'-' if gap is None else f'{gap:.0f} pts'} | {r['fell']}/{r['pairs']} | "
                     f"{fmt(r['reproduced'])} | {note} |")
    table = "\n".join(lines)
    print(table)
    (ROOT / "results" / "leaderboard.md").write_text("# Leaderboard\n\n" + table + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
