"""Does script_parser.py actually work end to end?

Nothing in script_parser.py has executed before this. In particular the
brief flagged two unverified assumptions: does response_schema accept the
nested pydantic model with a Literal field, and does response.parsed come
back populated on a real call rather than None.

What to look for:
  - parse_fdx: does the plain-text dump include every Scene Heading /
    Action / Dialogue line, correctly joined across multi-Text paragraphs
    (the bolded "sharp-eyed" and italic "Nike" runs in the fixture)?
  - extract_entities: does every real entity in the fixture get found
    (Nike, Bohemian Rhapsody/Queen, Starbucks, Golden Gate Bridge,
    Barack Obama) and every fictional one get skipped (Jules Moran, Frank
    Grimbleton, Grimbleton & Sons, "Moonlight on the Wather", Zorbex
    Corporation)?
  - Are entity_types sane (song vs brand vs person vs location)?
  - prepare_entities: does the dedup step collapse Nike's duplicate work
    items (scene heading vs action line) down to exactly one?

Run:  python smoke_test_script_parsing.py
"""

import os
import sys

from dotenv import load_dotenv

# Web/script text can contain characters cp1252 can't encode.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from clearance_agent.script_parser import parse_fdx, extract_entities  # noqa: E402
from clearance_agent.workflow import prepare_entities  # noqa: E402

if not os.environ.get("GOOGLE_API_KEY"):
    raise SystemExit("GOOGLE_API_KEY not set. Copy .env.example to .env and fill it in.")

FIXTURE = "scratch/test_script.fdx"

REAL_ENTITIES = {
    "Nike",
    "Bohemian Rhapsody",
    "Queen",
    "Starbucks",
    "Golden Gate Bridge",
    "Barack Obama",
    "Ferry Building",
}
FICTIONAL_ENTITIES = {
    "Jules Moran",
    "Jules",
    "Frank Grimbleton",
    "Frank",
    "Grimbleton & Sons",
    "Moonlight on the Wather",
    "Zorbex Corporation",
    "Zorbex",
}


def main() -> None:
    print("=" * 72)
    print(f"parse_fdx({FIXTURE!r})")
    print("=" * 72)
    script_text = parse_fdx(FIXTURE)
    print(script_text)

    print("\n" + "=" * 72)
    print("extract_entities(...)")
    print("=" * 72)
    entities = extract_entities(script_text)
    for e in entities:
        print(f"  {e['entity_type']:10s}  {e['entity']}")

    found_names = {e["entity"] for e in entities}

    print("\n" + "=" * 72)
    print("prepare_entities(entities) — dedup step")
    print("=" * 72)
    deduped = prepare_entities(entities)
    for e in deduped:
        print(f"  {e['entity_type']:10s}  {e['entity']}")
    nike_items = [e for e in deduped if "nike" in e["entity"].lower()]

    print("\n\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)

    missed_real = [n for n in REAL_ENTITIES if not any(
        n.lower() in f.lower() or f.lower() in n.lower() for f in found_names
    )]
    wrongly_included = [n for n in found_names if any(
        n.lower() in fic.lower() or fic.lower() in n.lower() for fic in FICTIONAL_ENTITIES
    )]

    print(f"  Entities extracted (pre-dedup): {len(entities)}")
    print(f"  Entities after dedup: {len(deduped)}")
    print(f"  Nike work items after dedup: {len(nike_items)} {nike_items}")
    print(f"  Real entities apparently missed: {missed_real or 'none'}")
    print(f"  Fictional entities apparently leaked through: {wrongly_included or 'none'}")
    print("  Manually confirm entity_type assignments above look sane")
    print("  (song for Bohemian Rhapsody/Queen, brand for Nike/Starbucks,")
    print("   person for Barack Obama, location for Golden Gate Bridge).")
    print("=" * 72)


if __name__ == "__main__":
    main()
