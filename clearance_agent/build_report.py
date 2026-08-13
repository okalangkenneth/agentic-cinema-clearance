"""Verified findings -> a self-contained HTML clearance report.

Format decision: HTML, not the PDF named in the original architecture
sketch. Recorded explicitly rather than silently diverging:
no PDF library is installed (checked: weasyprint, reportlab, fpdf2,
pdfkit, xhtml2pdf are all absent from this environment) and this stage
doesn't need one. HTML with inline CSS renders directly in the browser used
to record the demo video, needs zero new dependencies, and lets every
citation be an actual clickable link: a PDF viewer inside a screen
recording is a worse demo of "every claim shows its source" than a browser
tab where a judge can see the link target in the status bar. No templating
library either (jinja2 is not installed), so findings are structured dicts
from verify_finding.py, not free text, so f-strings plus html.escape() on
every interpolated value are enough and add nothing to audit.

Ordering: findings needing attention first. A line producer scanning this
during a shoot week wants to know what's blocking them, not read
alphabetically. Order is ERROR (research itself failed; unresolved, needs
a human to re-run or investigate) > RED > AMBER > GREEN, entity name
alphabetically within each tier for a stable, scannable read.

verification_notes placement: inside a native <details> block per entity,
collapsed by default. Not the main body (they're about what was DROPPED,
not the finding itself, and foregrounding them ahead of what survived would
bury the actual clearance answer) and not omitted (the fact that claims
were dropped is evidence the verification pass is working, and an auditor
needs to be able to find it). <details> needs no JavaScript and is visible
on demand with one click, which is enough for both a live demo and a real
review.

Near-identical-name grouping (added after a real full-script run produced
three separate rows, "Nike", "NIKE OUTLET STORE", "Nike swoosh", for one
clearance question): deliberately a REPORT-layer concern, not a research-
layer one. workflow.py's pre-research dedup stays exact-match-only, on
purpose; merging before research risks folding two genuinely distinct
entities into one search, which is a worse failure than three separate
rows. Grouping happens here instead, AFTER every entity has already been
independently researched and verified, using a small Gemini call
(_group_entity_names below) that reasons about MEANING rather than string
overlap, so it can group "NIKE OUTLET STORE" with "Nike" without also
grouping "Apple" the brand with "Apple Valley Road" the street just because
they share a substring. This is the same "graph edges don't decide, but a
node's own function may call a model" pattern prepare_entities already uses
for extraction; the Workflow's graph structure (edges, nodes) is unchanged.
Every grouping decision is validated programmatically before being trusted
(exact set-match against the input names) and falls back to no grouping on
any failure, so a bad or failed call degrades to today's behavior rather
than losing or inventing an entity.
"""

import html
from datetime import datetime, timezone

from google import genai
from google.genai import types
from pydantic import BaseModel

from .config import MODEL, SAFETY_SETTINGS

NOT_ESTABLISHED = "not established by sources"

_RISK_ORDER = {"ERROR": 0, "RED": 1, "AMBER": 2, "GREEN": 3}
_RISK_LABEL = {
    "ERROR": "NEEDS ATTENTION",
    "RED": "RED",
    "AMBER": "AMBER",
    "GREEN": "GREEN",
}
_RISK_COLOR = {
    "ERROR": "#6b7280",
    "RED": "#b91c1c",
    "AMBER": "#b45309",
    "GREEN": "#15803d",
}

_CLAIM_FIELDS = (
    ("rights_holder", "Rights holder"),
    ("registration_or_serial_number", "Registration / serial number"),
    ("registration_status", "Registration status"),
    ("license_required", "Licence required"),
)


def _risk_of(finding: dict) -> str:
    if finding.get("status") != "ok":
        return "ERROR"
    risk = finding.get("risk", "AMBER")
    return risk if risk in ("RED", "AMBER", "GREEN") else "AMBER"


def _e(value) -> str:
    """Escape for safe HTML interpolation. Claim values and excerpts pass
    through web content and model output; never trust them as markup."""
    return html.escape(str(value), quote=True)


def _claim_html(value: str, source_url: str) -> str:
    if not value or value.strip().lower() == NOT_ESTABLISHED:
        return '<span class="not-established">NOT ESTABLISHED BY SOURCES</span>'
    if source_url:
        return f'{_e(value)} <a class="cite" href="{_e(source_url)}" target="_blank" rel="noopener">[source]</a>'
    # Grounded value with no source_url should not happen post-verification,
    # but render plainly rather than hide it if it ever does.
    return _e(value)


def _entity_anchor(entity: str, index: int) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in entity.lower()).strip("-")
    return f"entity-{index}-{slug or 'unnamed'}"


class _EntityGroups(BaseModel):
    groups: list[list[str]]


_GROUP_PROMPT = """\
Below are entity names extracted separately from ONE screenplay and
researched independently. Some may be different surface-form mentions of
the SAME real-world clearance subject: a scene heading naming a store, an
action line naming a design mark, and a dialogue line naming the brand can
all refer to one thing a production would clear ONCE, not three times.

Group names that clearly refer to the SAME underlying clearance subject.
Do NOT group names that merely share a word but refer to different real-world
things: "Apple" the brand and "Apple Valley Road" the street share a word
and must NOT be grouped. If you are not confident two names are the same
subject, put them in separate groups; a missed grouping costs nothing, a
wrong one hides a real clearance question.

Every name below must appear in exactly one group. A name with nothing to
group with is its own group of one. Use the names EXACTLY as given, do not
reword or invent any.

Names:
{names_block}
"""


def _group_entity_names(names: list[str]) -> list[list[str]] | None:
    """Ask Gemini which entity names refer to the same clearance subject.

    Returns None (never a partial/guessed result) on ANY failure: a network
    or quota error, a blocked response, or a response that does not
    validate. Validation requires the returned groups to cover every input
    name EXACTLY once, no inventions, no omissions, no duplicates. This is
    the same "don't trust model behavior, check it in code" pattern
    verify_finding.py uses for claims, applied to a grouping decision
    instead: not proof the check produces the RIGHT groups, but a guarantee
    it never silently drops or fabricates an entity even if the model
    misbehaves.

    A broad except is deliberate and different from verify_finding.py's
    narrow quota-only catch: grouping only affects report PRESENTATION.
    Falling back to "no grouping" on any failure reproduces exactly today's
    already-acceptable behavior (one row per entity), never a wrong answer
    shipped silently, so there is nothing here worth distinguishing failure
    modes for.
    """
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=MODEL,
            contents=_GROUP_PROMPT.format(names_block="\n".join(f"- {n}" for n in names)),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_EntityGroups,
                safety_settings=SAFETY_SETTINGS,
            ),
        )
        parsed = response.parsed
        if parsed is None:
            return None
        groups = [g for g in parsed.groups if g]
        flat = [n for g in groups for n in g]
        if sorted(flat) != sorted(names) or len(flat) != len(set(flat)):
            return None
        return groups
    except Exception:
        return None


def _worst_risk(members: list[dict]) -> str:
    """The most urgent risk tier among a group's members.

    _RISK_ORDER ranks ERROR (0) as most urgent through GREEN (3) as least,
    so "worst" is the MINIMUM order value, not the maximum.
    """
    return min((_risk_of(f) for f in members), key=lambda r: _RISK_ORDER[r])


def _canonical_member(members: list[dict]) -> dict:
    """Which member's name headlines a group in the summary table.

    Shortest name wins, tie-broken alphabetically: a purely mechanical,
    non-fuzzy rule applied AFTER the grouping decision is already made and
    validated, so it only affects which already-grouped name displays
    first, never which entities get grouped together.
    """
    return min(members, key=lambda f: (len(f.get("entity", "")), f.get("entity", "")))


def _group_findings(findings: list[dict]) -> list[list[dict]]:
    """Findings -> groups of findings sharing one clearance subject.

    ERROR-status findings (research itself failed) are never grouped; they
    need direct attention regardless of what any sibling entity resolved
    to, and grouping decisions are made from resolved rights_holder values
    that error findings do not have. Skips the call entirely for fewer
    than two eligible findings; there is nothing to group.
    """
    ok = [f for f in findings if f.get("status") == "ok"]
    errored = [f for f in findings if f.get("status") != "ok"]
    groups = [[f] for f in errored]

    if len(ok) < 2:
        return groups + [[f] for f in ok]

    by_name = {f.get("entity", ""): f for f in ok}
    name_groups = _group_entity_names(list(by_name.keys()))
    if name_groups is None:
        return groups + [[f] for f in ok]

    return groups + [[by_name[n] for n in g] for g in name_groups]


def _summary_row(members: list[dict], anchor: str) -> str:
    risk = _worst_risk(members)
    canonical = _canonical_member(members)
    others = [m.get("entity", "") for m in members if m is not canonical]
    name_html = _e(canonical.get("entity", ""))
    if others:
        name_html += f' <span class="also-appears">(+{len(others)}: {_e(", ".join(others))})</span>'
    rights_holder = canonical.get("rights_holder", {}).get("value", NOT_ESTABLISHED)
    if rights_holder.strip().lower() == NOT_ESTABLISHED:
        rights_holder_cell = '<span class="not-established">not established</span>'
    else:
        rights_holder_cell = _e(rights_holder)
    return f"""
    <tr>
      <td><span class="badge" style="background:{_RISK_COLOR[risk]}">{_RISK_LABEL[risk]}</span></td>
      <td><a href="#{anchor}">{name_html}</a></td>
      <td>{_e(canonical.get('entity_type', ''))}</td>
      <td>{rights_holder_cell}</td>
    </tr>"""


def _finding_body(finding: dict) -> str:
    """The claims/reasoning/near-misses/notes block for ONE finding.

    Factored out of the group section below so every surface form in a
    group renders its own full, independently-verified detail: grouping
    changes how entities are PRESENTED, never what was found or dropped
    for any one of them.
    """
    if finding.get("status") != "ok":
        error_note = finding.get("verification_notes", ["research stage failed"])
        body = f'<p class="error-note">{_e("; ".join(error_note))}</p>'
    else:
        claim_rows = "\n".join(
            f'<div class="claim"><span class="claim-label">{label}:</span> '
            f'{_claim_html(finding.get(field, {}).get("value", ""), finding.get(field, {}).get("source_url", ""))}</div>'
            for field, label in _CLAIM_FIELDS
        )
        near_misses = finding.get("discarded_near_misses") or []
        near_miss_html = ""
        if near_misses:
            items = "\n".join(f"<li>{_e(nm)}</li>" for nm in near_misses)
            near_miss_html = f"""
            <div class="near-misses">
              <span class="claim-label">Discarded near-misses:</span>
              <ul>{items}</ul>
            </div>"""
        reasoning = _e(finding.get("risk_reasoning", ""))
        body = f"""
        {claim_rows}
        <div class="claim"><span class="claim-label">Risk reasoning:</span> {reasoning}</div>
        {near_miss_html}"""

    # For a research-stage error, the error text already shown above IS the
    # (only) verification note; showing it a second time in the details
    # block below would be pure duplication, not additional audit info.
    notes = finding.get("verification_notes") or [] if finding.get("status") == "ok" else []
    notes_html = ""
    if notes:
        items = "\n".join(f"<li>{_e(n)}</li>" for n in notes)
        notes_html = f"""
        <details class="verification-notes">
          <summary>Verification notes ({len(notes)} claim(s) dropped during verification)</summary>
          <ul>{items}</ul>
        </details>"""

    return body + notes_html


def _entity_section(members: list[dict], anchor: str) -> str:
    canonical = _canonical_member(members)
    risk = _worst_risk(members)
    entity = canonical.get("entity", "(unnamed entity)")
    entity_type = canonical.get("entity_type", "")

    if len(members) == 1:
        # The common case: render exactly as a single entity always has,
        # no grouping chrome for something that was not grouped with
        # anything.
        content = _finding_body(members[0])
    else:
        others = [m.get("entity", "") for m in members if m is not canonical]
        also_appears = (
            f'<p class="also-appears">Also appears in the script as: {_e(", ".join(others))}.</p>'
        )
        # Every member gets its OWN sub-heading and full detail block, not
        # a blended/averaged one: the invariant is that no surface form
        # loses its individually verified claims by being grouped.
        member_blocks = "\n".join(
            f"""
            <div class="group-member">
              <h3>{_e(m.get('entity', ''))} <span class="entity-type">({_e(m.get('entity_type', ''))})</span></h3>
              {_finding_body(m)}
            </div>"""
            for m in members
        )
        content = also_appears + member_blocks

    return f"""
    <section class="entity" id="{anchor}">
      <h2><span class="badge" style="background:{_RISK_COLOR[risk]}">{_RISK_LABEL[risk]}</span> {_e(entity)}
        <span class="entity-type">({_e(entity_type)})</span></h2>
      {content}
    </section>"""


_STYLE = """
body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
       max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1f2937; }
h1 { margin-bottom: 0.25rem; }
.subtitle { color: #6b7280; margin-top: 0; }
table { border-collapse: collapse; width: 100%; margin: 1.5rem 0; }
th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e5e7eb; }
th { background: #f9fafb; }
.badge { color: white; border-radius: 4px; padding: 0.15rem 0.5rem;
         font-size: 0.75rem; font-weight: 600; letter-spacing: 0.03em; }
.entity { border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem 1.25rem;
          margin-bottom: 1rem; }
.entity h2 { margin-top: 0; font-size: 1.1rem; }
.entity-type { color: #6b7280; font-weight: 400; font-size: 0.85rem; }
.claim { margin: 0.4rem 0; }
.claim-label { font-weight: 600; margin-right: 0.35rem; }
.not-established { font-style: italic; color: #6b7280; background: #f3f4f6;
                    border-radius: 4px; padding: 0.05rem 0.4rem; }
.cite { font-size: 0.85rem; }
.error-note { color: #b91c1c; }
.near-misses ul, .verification-notes ul { margin: 0.3rem 0 0 0; padding-left: 1.25rem; }
.verification-notes { margin-top: 0.5rem; font-size: 0.9rem; color: #4b5563; }
.verification-notes summary { cursor: pointer; font-weight: 600; }
.also-appears { color: #6b7280; font-size: 0.85rem; font-weight: 400; }
.group-member { margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px dashed #e5e7eb; }
.group-member h3 { margin: 0 0 0.35rem 0; font-size: 0.95rem; }
footer { color: #6b7280; font-size: 0.8rem; margin-top: 2rem;
         border-top: 1px solid #e5e7eb; padding-top: 1rem; }
"""


def build_report(findings: list[dict] | dict, *, script_name: str = "") -> str:
    """Verified findings (JoinNode's collected list) -> a self-contained
    HTML report string.

    Accepts either the raw list, or the dict shape the ADK Runner hands the
    node downstream of a JoinNode ({"<upstream_node_name>": [...]}); the
    node wiring in workflow.py passes node_input straight through rather
    than assuming the wrapping key, since that key is just the upstream
    node's name and shouldn't be load-bearing here.
    """
    if isinstance(findings, dict):
        lists = [v for v in findings.values() if isinstance(v, list)]
        findings = lists[0] if lists else []
    if not isinstance(findings, list):
        findings = []

    groups = _group_findings(findings)
    ordered_groups = sorted(
        groups,
        key=lambda g: (_RISK_ORDER[_worst_risk(g)], _canonical_member(g).get("entity", "").lower()),
    )
    anchors = [_entity_anchor(_canonical_member(g).get("entity", ""), i) for i, g in enumerate(ordered_groups)]

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"Clearance Report: {script_name}" if script_name else "Clearance Report"

    # Counts are per GROUP (clearance question), matching what a reader
    # actually sees as rows in the summary table below, not per surface
    # form. A group with 3 members counted 3 times here would say "8 red"
    # over a table with only 6 visible rows: a self-inflicted accuracy gap
    # of exactly the kind this report exists to prevent.
    counts = {"ERROR": 0, "RED": 0, "AMBER": 0, "GREEN": 0}
    for g in ordered_groups:
        counts[_worst_risk(g)] += 1

    summary_rows = "\n".join(_summary_row(g, a) for g, a in zip(ordered_groups, anchors))
    entity_sections = "\n".join(_entity_section(g, a) for g, a in zip(ordered_groups, anchors))

    total_mentions = len(findings)
    total_groups = len(ordered_groups)
    entity_count_label = (
        f"{total_groups} entities ({total_mentions} mentions grouped)"
        if total_mentions != total_groups
        else f"{total_groups} entities"
    )

    if not ordered_groups:
        body_html = '<p>No entities were found requiring clearance research.</p>'
    else:
        body_html = f"""
        <table>
          <thead><tr><th>Risk</th><th>Entity</th><th>Type</th><th>Rights holder</th></tr></thead>
          <tbody>{summary_rows}</tbody>
        </table>
        {entity_sections}"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_e(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>{_e(title)}</h1>
<p class="subtitle">Generated {generated} &middot; {entity_count_label} &middot;
   {counts['ERROR']} needs attention &middot; {counts['RED']} red &middot;
   {counts['AMBER']} amber &middot; {counts['GREEN']} green</p>
{body_html}
<footer>
Research output for production legal review, not legal advice. Every claim
above is grounded in the source cited beside it; anything the sources did
not establish is marked "NOT ESTABLISHED BY SOURCES" rather than left
blank or filled from other knowledge.
</footer>
</body>
</html>"""
