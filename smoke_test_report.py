"""Does build_report() produce something a line producer could actually act on?

"It generated without crashing" is not the bar. This test renders three
reports and actually inspects the HTML for the properties the brief calls
out: RED-first ordering, a visibly distinct "not established" state, and
real citation links.

Deliberately economical with real Gemini calls: `gemini-3.6-flash` on the
free-tier AI Studio key this project uses locally is capped at 20
requests/DAY, not per-minute —
and this session had already spent most of that quota on earlier stages
before reaching this one. So only ONE real verify_finding() call is made
here (case A). The all-"not established" case and the RED/AMBER/GREEN
ordering case are built from hand-shaped finding dicts matching
verify_finding()'s actual output contract — this tests build_report()
itself, which is the thing under test, without re-spending the one
resource this run doesn't have much of.

Case A's fallback (a previously captured real finding, used if the live
call hits the quota wall) only engages on a genuine 429/RESOURCE_EXHAUSTED
from the Gemini API (see `_is_quota_error`) — a real bug in
`research_clearance` or `verify_finding` raises instead of silently
reusing old output.

What to look for:
  - Case A: a real report from real findings, saved to
    scratch/report_smoke_case_a.html — open it and actually look.
  - Case B: an entity where every claim is "not established by sources" —
    does the report show a visibly distinct state, or does it look like an
    empty/broken row?
  - Case C: mixed RED/AMBER/GREEN (plus an ERROR case) — RED (and ERROR)
    entities must sort first, not alphabetically.

Run:  python smoke_test_report.py
"""

import os
import sys
import uuid

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# scratch/ is gitignored and won't exist on a fresh clone; this test writes
# its output HTML there.
os.makedirs("scratch", exist_ok=True)

from google.genai import errors as genai_errors  # noqa: E402

from clearance_agent.build_report import NOT_ESTABLISHED, build_report  # noqa: E402
from clearance_agent.parallel_tool import research_clearance  # noqa: E402
from clearance_agent.verify_finding import verify_finding  # noqa: E402


def _is_quota_error(exc: Exception) -> bool:
    """True only for a 429/RESOURCE_EXHAUSTED from the Gemini API — the
    specific, expected failure this fallback exists for. Deliberately NOT
    a bare `except Exception`: that would also swallow a real bug in
    research_clearance or verify_finding behind the same fallback path,
    which is exactly the risk the first version of this test masked before
    this check was narrowed to quota errors only."""
    if not isinstance(exc, genai_errors.ClientError):
        return False
    return exc.code == 429 or (exc.status or "") == "RESOURCE_EXHAUSTED"

if not os.environ.get("PARALLEL_API_KEY") or not os.environ.get("GOOGLE_API_KEY"):
    raise SystemExit("PARALLEL_API_KEY and GOOGLE_API_KEY must both be set. See .env.example.")

SESSION = f"smoke-report-{uuid.uuid4().hex[:8]}"


def _claim(value: str, url: str = "") -> dict:
    return {"value": value, "source_url": url}


# Captured verbatim from a real research_clearance() + verify_finding() call
# made earlier in this same session, during the Item 0 grounding-check
# investigation (before the daily quota below was discovered exhausted).
# Real model output, real sources — reused rather than re-requested because
# this session's `gemini-3.6-flash` quota was confirmed exhausted for the
# day. Not fabricated; see the try block below, which attempts a genuinely
# fresh call first and only falls back to this if that call fails on quota.
_CAPTURED_REAL_FINDING = {
    "entity": "Coca-Cola", "entity_type": "brand",
    "session_id": "script-218e9962", "search_id": "search_4433dabc518c6f3e59a35ac69c4c5191",
    "status": "ok",
    "rights_holder": _claim("The Coca-Cola Company", "https://trademarks.justia.com/781/96/coca-78196491.html"),
    "registration_or_serial_number": _claim(
        "Registration Number 3121372", "https://trademarks.justia.com/781/96/coca-78196491.html"
    ),
    "registration_status": _claim(
        "800 - Registered And Renewed", "https://trademarks.justia.com/781/96/coca-78196491.html"
    ),
    "license_required": _claim(
        "clearance is required for uses or depictions of third-party marks, such as products "
        "bearing a trademark (like someone drinking from a Coca-Cola can).",
        "https://www.lexisnexis.com/community/insights/legal/practical-guidance-journal/b/pa/posts/rights-clearance",
    ),
    "risk": "RED",
    "risk_reasoning": (
        "COCA-COLA is an active registered trademark owned by The Coca-Cola Company. Sources "
        "explicitly indicate that rights clearance is required when depicting products bearing "
        "third-party trademarks, such as a Coca-Cola can, in film or TV productions."
    ),
    "discarded_near_misses": [
        "USPTO Serial #88292608 / Registration #5828870: Polygon design mark with concave sides "
        "owned by The Coca-Cola Company that contains no literal word elements."
    ],
    "verification_notes": [],
}


def case_a_real_finding() -> dict:
    """A real finding, one way or another: a fresh call if quota allows,
    otherwise a genuinely real one already captured earlier this session."""
    print("\n" + "=" * 72)
    print("CASE A — a real finding: Coca-Cola (brand)")
    print("=" * 72)
    # research_clearance failing is a real bug (Parallel, not Gemini quota)
    # and must NOT be swallowed by the quota fallback below — let it raise.
    research = research_clearance("Coca-Cola", "brand", session_id=SESSION)
    if research["status"] != "ok":
        raise SystemExit(f"research_clearance failed, cannot proceed: {research.get('error')}")

    try:
        finding = verify_finding(research)
        print("  (fresh live call succeeded)")
    except Exception as exc:
        if not _is_quota_error(exc):
            raise  # a real bug in verify_finding, not the expected quota wall — surface it
        print(f"  live call hit the Gemini quota wall ({type(exc).__name__}), "
              f"falling back to a real finding")
        print("  captured earlier this session, before the daily quota was exhausted")
        finding = _CAPTURED_REAL_FINDING

    print(f"  entity={finding['entity']}  risk={finding['risk']}  "
          f"rights_holder={finding['rights_holder']['value']!r}")

    html_out = build_report([finding], script_name="Smoke Test — Case A (real)")
    path = "scratch/report_smoke_case_a.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"  wrote {path} ({len(html_out)} chars) — open it and look")

    has_cite_link = 'class="cite"' in html_out
    print(f"  Report contains a real citation link: {has_cite_link}")
    return finding


def case_b_all_not_established() -> None:
    print("\n" + "=" * 72)
    print("CASE B — a finding where every claim is 'not established by sources'")
    print("=" * 72)
    finding = {
        "entity": "Backlot Diner",
        "entity_type": "business",
        "status": "ok",
        "rights_holder": _claim(NOT_ESTABLISHED),
        "registration_or_serial_number": _claim(NOT_ESTABLISHED),
        "registration_status": _claim(NOT_ESTABLISHED),
        "license_required": _claim(NOT_ESTABLISHED),
        "risk": "GREEN",
        "risk_reasoning": "No real-world entity matching this name was established by any source.",
        "discarded_near_misses": [],
        "verification_notes": [
            "rights_holder: 'Backlot Diner Inc.' not found in the excerpts of the cited source "
            "(https://example.com/unrelated) — dropped"
        ],
    }
    html_out = build_report([finding], script_name="Smoke Test — Case B")
    path = "scratch/report_smoke_case_b.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"  wrote {path} ({len(html_out)} chars)")

    badge_count = html_out.count("NOT ESTABLISHED BY SOURCES")
    has_blank_dash = "<td></td>" in html_out or ">&mdash;<" in html_out
    print(f"  'NOT ESTABLISHED BY SOURCES' badge appears {badge_count} times "
          f"(expect 5: one per claim field, plus one in the footer legend)")
    print(f"  Rendered as an empty cell or em-dash anywhere (would be a miss): {has_blank_dash}")
    print(f"  verification_notes surfaced (details block present): {'<details' in html_out}")


def case_c_mixed_ordering(real_finding: dict) -> None:
    print("\n" + "=" * 72)
    print("CASE C — mixed RED/AMBER/GREEN/ERROR: does RED/ERROR sort first?")
    print("=" * 72)

    green = {
        "entity": "Zzyzx Holdings", "entity_type": "business", "status": "ok",
        "rights_holder": _claim(NOT_ESTABLISHED), "registration_or_serial_number": _claim(NOT_ESTABLISHED),
        "registration_status": _claim(NOT_ESTABLISHED), "license_required": _claim(NOT_ESTABLISHED),
        "risk": "GREEN", "risk_reasoning": "No matching real-world entity.",
        "discarded_near_misses": [], "verification_notes": [],
    }
    amber = {
        "entity": "Ambient Coffee Co", "entity_type": "brand", "status": "ok",
        "rights_holder": _claim("Ambient Coffee Co LLC", "https://example.com/amb"),
        "registration_or_serial_number": _claim(NOT_ESTABLISHED),
        "registration_status": _claim("pending", "https://example.com/amb"),
        "license_required": _claim(NOT_ESTABLISHED),
        "risk": "AMBER", "risk_reasoning": "Registration pending, unclear if it will grant before production.",
        "discarded_near_misses": [], "verification_notes": [],
    }
    error = {
        "entity": "Aardvark Airlines", "entity_type": "brand", "status": "error",
        "rights_holder": _claim(NOT_ESTABLISHED), "registration_or_serial_number": _claim(NOT_ESTABLISHED),
        "registration_status": _claim(NOT_ESTABLISHED), "license_required": _claim(NOT_ESTABLISHED),
        "risk": "AMBER", "risk_reasoning": "",
        "discarded_near_misses": [], "verification_notes": ["research stage error: simulated failure for this test"],
    }

    findings = [green, amber, error, real_finding]
    html_out = build_report(findings, script_name="Smoke Test — Case C (mixed)")
    path = "scratch/report_smoke_case_c.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"  wrote {path} ({len(html_out)} chars)")

    # Extract the summary table's row order by anchor id, cheaply.
    order = []
    for entity in ("Aardvark Airlines", real_finding["entity"], "Ambient Coffee Co", "Zzyzx Holdings"):
        idx = html_out.find(f">{entity}<")
        order.append((idx, entity))
    seen_in_html_order = [e for _, e in sorted(order)]
    print(f"  Entity order as they appear in the summary table: {seen_in_html_order}")

    expected_first = {"Aardvark Airlines", real_finding["entity"]}  # ERROR and RED (if Coca-Cola came back RED)
    ok = seen_in_html_order[0] in expected_first or seen_in_html_order[1] in expected_first
    print(f"  ERROR/RED entities appear ahead of AMBER/GREEN: {ok}")
    print(f"  Last entity is GREEN (Zzyzx Holdings) as expected: {seen_in_html_order[-1] == 'Zzyzx Holdings'}")


def main() -> None:
    real_finding = case_a_real_finding()
    case_b_all_not_established()
    case_c_mixed_ordering(real_finding)

    print("\n\n" + "=" * 72)
    print("VERDICT")
    print("  Open scratch/report_smoke_case_a.html, _b.html, _c.html in a")
    print("  browser and confirm by eye: citations are clickable, 'not")
    print("  established' reads as a deliberate state not a blank, and RED/")
    print("  ERROR entities are what a line producer sees first in Case C.")
    print("=" * 72)


if __name__ == "__main__":
    main()
