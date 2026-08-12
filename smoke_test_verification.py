"""Does verify_finding() actually catch an unsupported claim?

Nothing in verify_finding.py has executed before this. The whole reason
this pass exists is a documented failure: told in plain instructions to
write "not established by sources" for unsupported claims, the agent
instead wrote "Primary record label / owner of master recording (EMI
Music / Queen)" citing two generic music-licensing pages that establish
nothing about that specific master. A test that only
exercises the happy path proves nothing about whether this pass actually
holds that line — so this smoke test runs three cases:

  A. REAL research + real verification on a known-good entity (Coca-Cola).
     Sanity check: do grounded claims (rights holder, registration number)
     survive verification with a source_url attached?

  B. REAL verification against a HAND-BUILT research result that
     reproduces the documented bug's shape: generic music-licensing
     sources that never name a specific rights holder for this specific
     song. This is the actual failing case the brief asks for — if
     verify_finding lets a specific owner name through here, that is a
     miss worth recording, not a pass to wave through.

  C. A deterministic, no-API-call check of the programmatic layer itself
     (_check_claim / _grounded_in_source): feed it a claim asserting a
     value that provably is not in the cited source's excerpts, confirm
     it is rewritten to "not established by sources" regardless of what
     any model does. This is the part of the design that is NOT allowed
     to depend on model behavior.

Run:  python smoke_test_verification.py
"""

import os
import sys
import uuid

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from clearance_agent.parallel_tool import research_clearance  # noqa: E402
from clearance_agent.verify_finding import (  # noqa: E402
    NOT_ESTABLISHED,
    _Claim,
    _check_claim,
    verify_finding,
)

if not os.environ.get("PARALLEL_API_KEY") or not os.environ.get("GOOGLE_API_KEY"):
    raise SystemExit("PARALLEL_API_KEY and GOOGLE_API_KEY must both be set. See .env.example.")

SESSION = f"smoke-verify-{uuid.uuid4().hex[:8]}"


def show_finding(finding: dict) -> None:
    print(f"  status: {finding['status']}   risk: {finding.get('risk')}")
    for field in (
        "rights_holder",
        "registration_or_serial_number",
        "registration_status",
        "license_required",
    ):
        claim = finding[field]
        print(f"  {field}: {claim['value']!r}  <- {claim['source_url'] or '(no source)'}")
    if finding.get("discarded_near_misses"):
        print(f"  discarded_near_misses: {finding['discarded_near_misses']}")
    if finding.get("verification_notes"):
        print("  verification_notes (claims this pass dropped):")
        for n in finding["verification_notes"]:
            print(f"    - {n}")


def case_a_real_happy_path() -> bool:
    print("\n" + "=" * 72)
    print("CASE A — real research + real verification: Coca-Cola (brand)")
    print("=" * 72)
    research_result = research_clearance("Coca-Cola", "brand", session_id=SESSION)
    if research_result["status"] != "ok":
        print(f"  !! research_clearance failed: {research_result.get('error')}")
        return False

    finding = verify_finding(research_result)
    show_finding(finding)

    ok = finding["rights_holder"]["value"] != NOT_ESTABLISHED and bool(finding["rights_holder"]["source_url"])
    print(f"\n  Grounded rights_holder claim with a source_url? {ok}")
    return ok


def case_b_real_unsupported_claim() -> bool:
    print("\n" + "=" * 72)
    print("CASE B — real verification against the documented failing shape")
    print("=" * 72)
    print("  (two generic music-licensing pages, neither names a specific")
    print("   rights holder for this specific song — reproduces the EMI/")
    print("   Queen incident's source shape without editorializing an answer)")

    research_result = {
        "status": "ok",
        "entity": "Bohemian Rhapsody",
        "entity_type": "song",
        "search_id": "fake-search-id",
        "session_id": SESSION,
        "sources": [
            {
                "url": "https://example.com/music-licensing-101",
                "title": "Music Licensing 101 for Film Productions",
                "publish_date": "",
                "excerpts": [
                    "Before using any pre-existing song in a film, a production "
                    "must clear two separate rights: the composition (the "
                    "underlying song) and the master recording (the specific "
                    "performance). These are often controlled by different "
                    "parties and must be licensed independently. Classic rock "
                    "catalogs from the 1970s, including material associated "
                    "with acts like Queen, are commonly represented by major "
                    "publishers such as EMI or Sony/ATV, though the specific "
                    "publisher varies by title and territory.",
                ],
                "is_registry_mirror": False,
                "identifiers": {"serial_numbers": [], "registration_numbers": [], "tsdr_urls": []},
            },
            {
                "url": "https://example.com/sync-licensing-explained",
                "title": "What Is a Synchronization License?",
                "publish_date": "",
                "excerpts": [
                    "A synchronization license grants permission to pair music "
                    "with visual media. Productions typically work with a music "
                    "supervisor or clearance house to identify and contact the "
                    "relevant publisher and label for any track under "
                    "consideration. EMI Music Publishing is one of the "
                    "largest administrators of catalog rights globally.",
                ],
                "is_registry_mirror": False,
                "identifiers": {"serial_numbers": [], "registration_numbers": [], "tsdr_urls": []},
            },
        ],
        "warnings": [],
    }

    finding = verify_finding(research_result)
    show_finding(finding)

    caught = finding["rights_holder"]["value"] == NOT_ESTABLISHED
    if not caught:
        print(
            "\n  !! MISS: rights_holder survived verification as "
            f"{finding['rights_holder']['value']!r}, cited to "
            f"{finding['rights_holder']['source_url']!r}, but neither source "
            "excerpt names a specific rights holder. Check whether that "
            "phrase is genuinely a substring of the cited excerpt (a real "
            "grounding) or whether the substring check is too permissive."
        )
    else:
        print("\n  Caught: rights_holder correctly forced to 'not established by sources'.")
    return caught


def case_c_deterministic_programmatic_check() -> bool:
    print("\n" + "=" * 72)
    print("CASE C — deterministic check of _check_claim, no API call")
    print("=" * 72)

    sources_by_url = {
        "https://example.com/real-source": {
            "url": "https://example.com/real-source",
            "excerpts": ["Nike, Inc. is the registered owner of this mark."],
            "identifiers": {"serial_numbers": ["73456789"], "registration_numbers": ["1234567"]},
        }
    }
    notes: list[str] = []

    # A claim whose value is provably absent from the cited source's excerpts.
    fabricated = _Claim(value="Acme Global Holdings", source_url="https://example.com/real-source")
    result = _check_claim(fabricated, sources_by_url, notes, "rights_holder")
    print(f"  fabricated claim -> {result}")
    print(f"  notes -> {notes}")

    # A claim citing a url that isn't in this entity's own source list at all.
    notes2: list[str] = []
    wrong_url = _Claim(value="Nike, Inc.", source_url="https://example.com/not-a-real-source")
    result2 = _check_claim(wrong_url, sources_by_url, notes2, "rights_holder")
    print(f"  wrong-source-url claim -> {result2}")
    print(f"  notes -> {notes2}")

    # A claim that IS grounded should survive unchanged.
    notes3: list[str] = []
    grounded = _Claim(value="Nike, Inc.", source_url="https://example.com/real-source")
    result3 = _check_claim(grounded, sources_by_url, notes3, "rights_holder")
    print(f"  grounded claim -> {result3}")

    ok = (
        result["value"] == NOT_ESTABLISHED
        and result2["value"] == NOT_ESTABLISHED
        and result3["value"] == "Nike, Inc."
    )
    print(f"\n  Fabricated and wrong-url claims dropped, grounded claim kept? {ok}")
    return ok


def main() -> None:
    a_ok = case_a_real_happy_path()
    b_ok = case_b_real_unsupported_claim()
    c_ok = case_c_deterministic_programmatic_check()

    print("\n\n" + "=" * 72)
    print("VERDICT")
    print("  A. Grounded happy-path claim survived with a citation:", a_ok)
    print("  B. Real model call on the documented failing shape correctly")
    print("     forced to 'not established by sources':", b_ok)
    print("  C. Programmatic layer deterministically drops a fabricated")
    print("     claim and a wrong-source-url claim, keeps a grounded one:", c_ok)
    print("=" * 72)


if __name__ == "__main__":
    main()
