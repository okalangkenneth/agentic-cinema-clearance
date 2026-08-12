"""Step 0 v2: does the revised search actually find rights holders + serials?

v1 (2026-08-08) tested registry-only domain filtering vs unfiltered.
Filtering lost badly — see scratch/smoke_2026-08-08.txt and the DESIGN NOTE
in parallel_tool.py. This version tests the real tool as it now stands.

What to look for:
  - IDENTIFIERS harvested? A serial number is the key to the primary record.
    No serial = we can't get past secondary sources.
  - Registry mirrors present among sources (marked *)?
  - Off-target results? v1 returned SPRITE for Coca-Cola and the 2018 FILM
    for the song. Disambiguation is the hard part of this product.

Run:  python smoke_test_parallel.py
"""

import os
import sys
import uuid

from dotenv import load_dotenv

# Web excerpts contain characters cp1252 can't encode (non-breaking hyphens,
# smart quotes). Without this, printing a result crashes on Windows.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from clearance_agent.parallel_tool import research_clearance  # noqa: E402
if not os.environ.get("PARALLEL_API_KEY"):
    raise SystemExit("PARALLEL_API_KEY not set. Copy .env.example to .env and fill it in.")

TEST_ENTITIES = [
    ("Coca-Cola", "brand"),
    ("Bohemian Rhapsody", "song"),
    ("Nike swoosh", "trademark"),
]

SESSION = f"smoke-{uuid.uuid4().hex[:8]}"


def show(entity: str, kind: str) -> None:
    print("\n" + "=" * 72)
    print(f"{entity}  ({kind})")
    print("=" * 72)

    res = research_clearance(entity, kind, session_id=SESSION)

    if res["status"] != "ok":
        print(f"!! FAILED  {res['error']}")
        return

    print(f"search_id={res['search_id']}  sources={len(res['sources'])}")
    if res["warnings"]:
        print(f"  WARNINGS: {res['warnings']}")

    print("\n  SOURCES  (* = registry mirror)")
    for i, s in enumerate(res["sources"], 1):
        mark = "*" if s["is_registry_mirror"] else " "
        ids = s["identifiers"]
        print(f"\n  {mark}[{i}] {s['title'] or '(no title)'}")
        print(f"      {s['url']}")
        if ids["serial_numbers"] or ids["registration_numbers"]:
            print(
                f"      ids: serial={ids['serial_numbers']} "
                f"reg={ids['registration_numbers']}"
            )
        for ex in s["excerpts"]:
            print(f"      | {ex[:300].replace(chr(10), ' ')}...")


def main() -> None:
    for entity, kind in TEST_ENTITIES:
        show(entity, kind)

    print("\n\n" + "=" * 72)
    print("VERDICT")
    print("  1. Serial number harvested for every entity?")
    print("  2. Registry mirrors (*) appearing in sources?")
    print("  3. Any off-target entities (wrong mark, wrong owner, the film")
    print("     instead of the song)? That is the disambiguation problem.")
    print("=" * 72)


if __name__ == "__main__":
    main()
