"""Build common/il_ilce.json from the Turkish Wikipedia article "Türkiye'nin ilçeleri".

Source : https://tr.wikipedia.org/wiki/Türkiye'nin_ilçeleri
License: CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)

The generated JSON is committed, so the database generator never needs network
access. Re-run this script only to refresh the list:

    python scripts/build_il_ilce.py            # download via the MediaWiki API
    python scripts/build_il_ilce.py dump.json  # parse a saved API response
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "common" / "il_ilce.json"
PAGE = "Türkiye'nin ilçeleri"
API = (
    "https://tr.wikipedia.org/w/api.php?action=parse&prop=wikitext|revid"
    "&format=json&formatversion=2&page=" + urllib.parse.quote(PAGE)
)
REGIONS = {
    "AB": "Akdeniz", "DAB": "Doğu Anadolu", "EB": "Ege", "GDAB": "Güneydoğu Anadolu",
    "İAB": "İç Anadolu", "KB": "Karadeniz", "MB": "Marmara",
}


def fetch() -> dict:
    req = urllib.request.Request(API, headers={"User-Agent": "turkish-sql-bench/0.1 (research dataset)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def link_text(cell: str) -> str:
    """'[[Iğdır (il)|Iğdır]]' -> 'Iğdır'; "'''[[Ceyhan]]'''" -> 'Ceyhan'."""
    cell = cell.replace("'''", "").strip()
    m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", cell)
    if m:
        return (m.group(2) or m.group(1)).strip()
    return cell


def cell_value(cell: str) -> str:
    # '| style="text-align:right" | 15.839' -> '15.839'
    return cell.split("|")[-1].strip()


def parse(wikitext: str) -> list[dict]:
    table = wikitext[wikitext.index("{|"):]
    table = table[: table.index("\n|}")]
    rows = table.split("\n|-")[1:]
    out = []
    for row in rows:
        cells = [c[1:] for c in row.strip("\n").split("\n") if c.startswith("|")]
        il = link_text(cells[0])
        ilce = link_text(cells[1])
        bolge = link_text(cells[2])
        nufus = cell_value(cells[5]).replace(".", "")
        out.append({
            "il": il,
            "ilce": ilce,
            "bolge": REGIONS.get(bolge, bolge),
            "nufus_2025": int(nufus) if nufus.isdigit() else None,
        })
    return out


def main() -> None:
    if len(sys.argv) > 1:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        data = fetch()
    rows = parse(data["parse"]["wikitext"])
    iller: dict[str, dict] = {}
    for r in rows:
        il = iller.setdefault(r["il"], {"il": r["il"], "bolge": r["bolge"], "ilceler": []})
        il["ilceler"].append({"ilce": r["ilce"], "nufus_2025": r["nufus_2025"]})
    doc = {
        "source": "https://tr.wikipedia.org/wiki/T%C3%BCrkiye%27nin_il%C3%A7eleri",
        "source_revision": data["parse"].get("revid"),
        "license": "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)",
        "attribution": "Wikipedia contributors, \"Türkiye'nin ilçeleri\", Vikipedi, Özgür Ansiklopedi",
        "il_sayisi": len(iller),
        "ilce_sayisi": len(rows),
        "iller": list(iller.values()),
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(iller)} il, {len(rows)} ilçe")


if __name__ == "__main__":
    main()
