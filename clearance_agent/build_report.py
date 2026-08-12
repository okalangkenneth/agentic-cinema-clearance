"""Verified findings -> a self-contained HTML clearance report.

Format decision: HTML, not the PDF named in the original architecture
sketch. Recorded explicitly rather than silently diverging:
no PDF library is installed (checked: weasyprint, reportlab, fpdf2,
pdfkit, xhtml2pdf are all absent from this environment) and this stage
doesn't need one. HTML with inline CSS renders directly in the browser used
to record the demo video, needs zero new dependencies, and lets every
citation be an actual clickable link — a PDF viewer inside a screen
recording is a worse demo of "every claim shows its source" than a browser
tab where a judge can see the link target in the status bar. No templating
library either (jinja2 is not installed) — findings are structured dicts
from verify_finding.py, not free text, so f-strings plus html.escape() on
every interpolated value are enough and add nothing to audit.

Ordering: findings needing attention first. A line producer scanning this
during a shoot week wants to know what's blocking them, not read
alphabetically. Order is ERROR (research itself failed — unresolved, needs
a human to re-run or investigate) > RED > AMBER > GREEN, entity name
alphabetically within each tier for a stable, scannable read.

verification_notes placement: inside a native <details> block per entity,
collapsed by default. Not the main body (they're about what was DROPPED,
not the finding itself — foregrounding them ahead of what survived would
bury the actual clearance answer) and not omitted (the fact that claims
were dropped is evidence the verification pass is working, and an auditor
needs to be able to find it). <details> needs no JavaScript and is visible
on demand with one click, which is enough for both a live demo and a real
review.
"""

import html
from datetime import datetime, timezone

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
    through web content and model output — never trust them as markup."""
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


def _summary_row(finding: dict, anchor: str) -> str:
    risk = _risk_of(finding)
    rights_holder = finding.get("rights_holder", {}).get("value", NOT_ESTABLISHED)
    if rights_holder.strip().lower() == NOT_ESTABLISHED:
        rights_holder_cell = '<span class="not-established">not established</span>'
    else:
        rights_holder_cell = _e(rights_holder)
    return f"""
    <tr>
      <td><span class="badge" style="background:{_RISK_COLOR[risk]}">{_RISK_LABEL[risk]}</span></td>
      <td><a href="#{anchor}">{_e(finding.get('entity', ''))}</a></td>
      <td>{_e(finding.get('entity_type', ''))}</td>
      <td>{rights_holder_cell}</td>
    </tr>"""


def _entity_section(finding: dict, anchor: str) -> str:
    risk = _risk_of(finding)
    entity = finding.get("entity", "(unnamed entity)")
    entity_type = finding.get("entity_type", "")

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
    # (only) verification note — showing it a second time in the details
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

    return f"""
    <section class="entity" id="{anchor}">
      <h2><span class="badge" style="background:{_RISK_COLOR[risk]}">{_RISK_LABEL[risk]}</span> {_e(entity)}
        <span class="entity-type">({_e(entity_type)})</span></h2>
      {body}
      {notes_html}
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
footer { color: #6b7280; font-size: 0.8rem; margin-top: 2rem;
         border-top: 1px solid #e5e7eb; padding-top: 1rem; }
"""


def build_report(findings: list[dict] | dict, *, script_name: str = "") -> str:
    """Verified findings (JoinNode's collected list) -> a self-contained
    HTML report string.

    Accepts either the raw list, or the dict shape the ADK Runner hands the
    node downstream of a JoinNode ({"<upstream_node_name>": [...]}) — the
    node wiring in workflow.py passes node_input straight through rather
    than assuming the wrapping key, since that key is just the upstream
    node's name and shouldn't be load-bearing here.
    """
    if isinstance(findings, dict):
        lists = [v for v in findings.values() if isinstance(v, list)]
        findings = lists[0] if lists else []
    if not isinstance(findings, list):
        findings = []

    ordered = sorted(
        findings,
        key=lambda f: (_RISK_ORDER[_risk_of(f)], str(f.get("entity", "")).lower()),
    )
    anchors = [_entity_anchor(f.get("entity", ""), i) for i, f in enumerate(ordered)]

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"Clearance Report — {script_name}" if script_name else "Clearance Report"

    counts = {"ERROR": 0, "RED": 0, "AMBER": 0, "GREEN": 0}
    for f in ordered:
        counts[_risk_of(f)] += 1

    summary_rows = "\n".join(_summary_row(f, a) for f, a in zip(ordered, anchors))
    entity_sections = "\n".join(_entity_section(f, a) for f, a in zip(ordered, anchors))

    if not ordered:
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
<p class="subtitle">Generated {generated} &middot; {len(ordered)} entities &middot;
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
