"""Verification pass: research_clearance() output -> a finding with only
claims a cited source can actually support.

Why this exists (not just a stronger prompt): a first live run, told in
plain instructions to write "not established by sources" for unsupported
claims, instead wrote "Primary record label / owner of master recording
(EMI Music / Queen)" citing two generic music-licensing pages that
establish nothing about that specific master. Instructions alone did not
hold. This pass still uses a model to draft candidate claims (a plain regex
cannot recognise "who owns this song"), but every claim the model drafts is
then checked in code against the excerpts of the ONE source it cited before
being allowed to survive. A claim that fails that check is overwritten,
never silently dropped, so a report reader can tell "checked, found
nothing" from "not checked".

Per-source, never pooled: matches parallel_tool.py's
_harvest_identifiers docstring. A claim is checked against the excerpts of
the specific source it cites, not a blob of every source's excerpts.
Pooling is exactly the failure mode that attributed NAPSTER's serial number
to a Queen song when excerpts were pooled across sources in an earlier
version of this check.

Grounded-but-nonsensical values: a real full-script run verified Barack
Obama's rights_holder to "Obama May Have Few Rights Over Use Of Name", an
NPR headline. It passed _grounded_in_source because that string genuinely
is a substring of a cited excerpt; the check does what it says, which is
narrower than what a report reader needs. The model had correctly judged
that no single party clearly holds the rights, then described that
ambiguity in prose instead of using the NOT_ESTABLISHED escape hatch built
for exactly this. Fixed at the drafting stage: rights_holder now has an
explicit field description plus a prompt rule stating it is a NAME field,
nothing else, and that describing ambiguity belongs in risk_reasoning.
Re-running against a fresh set of real sources for the same entity 5 times
produced NOT_ESTABLISHED every time; no shape check was added on top, per
the brief's "prefer the cheapest fix that works" instruction. This does not
strengthen _grounded_in_source itself, which was never the problem: a value
can be perfectly grounded and still be the wrong shape for its field.
"""

import re
import sys
import time
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import MODEL, SAFETY_SETTINGS, describe_block_reason, is_quota_error

# Retry-on-429 for this call site, added after the 2026-08-13 rehearsal
# found 2 of 3 back-to-back Vertex runs dying here.
#
# Checked google.adk.workflow.RetryConfig by introspection before hand-
# rolling anything (per the working rule). It exists as a field on Node
# and DOES support retrying only on specific exception types (`exceptions:
# list[str | type[BaseException]]`). It does NOT fit here: the Gemini SDK
# raises the same `google.genai.errors.ClientError` class for the whole
# 4xx family (429, 400, 403, 404, ...), distinguished only by an
# instance attribute (`.code` / `.status`), not by a distinct exception
# type. RetryConfig can only filter by type, so `exceptions=[ClientError]`
# would also retry a real 400 (a malformed prompt, an actual bug) —
# exactly the "silently hide a real error" the brief warns against. There
# is no predicate/callable hook on RetryConfig to narrow it further.
# Wrapping the call by hand, reusing the same is_quota_error() check
# already proven narrow enough in smoke_test_report.py, is the fit here.
#
# No signal to size the backoff from: Vertex's 429 body carries no
# quotaId/retryDelay breakdown (unlike AI Studio's, which at least names a
# retryDelay even though that delay turned out to be misleading for the
# daily cap). These numbers are a plain exponential backoff, not derived
# from a documented Vertex limit.
_RETRY_MAX_ATTEMPTS = 5
_RETRY_INITIAL_DELAY = 5.0
_RETRY_BACKOFF_FACTOR = 2.0
_RETRY_MAX_DELAY = 30.0


def _generate_with_retry(client: genai.Client, **kwargs):
    """client.models.generate_content(**kwargs), retrying only on a 429/
    RESOURCE_EXHAUSTED, up to _RETRY_MAX_ATTEMPTS total attempts. Any other
    exception (including a non-quota ClientError) raises immediately on
    the first attempt — see the module docstring above for why this isn't
    RetryConfig."""
    delay = _RETRY_INITIAL_DELAY
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            if not is_quota_error(exc) or attempt == _RETRY_MAX_ATTEMPTS:
                raise
            wait = min(delay, _RETRY_MAX_DELAY)
            # Genuine progress, not faked: a real retry actually happening,
            # printed to stderr like ADK's own node-failure logging, so it's
            # visible during both this reliability measurement and a live
            # demo run rather than a silent stall the viewer can't explain.
            print(
                f"  [verify_finding] 429, retry {attempt}/{_RETRY_MAX_ATTEMPTS - 1} "
                f"in {wait:.0f}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay *= _RETRY_BACKOFF_FACTOR
    raise AssertionError("unreachable")  # loop always returns or raises

_RISK = Literal["RED", "AMBER", "GREEN"]

NOT_ESTABLISHED = "not established by sources"


class _Claim(BaseModel):
    value: str = Field(
        description=(
            f"The claim's value, exactly as stated by the cited source. "
            f"If no source in the list below supports this claim, write "
            f'exactly "{NOT_ESTABLISHED}" and leave source_url empty.'
        )
    )
    source_url: str = Field(
        default="",
        description=(
            "The exact url, copied verbatim from the numbered source list "
            "below, of the ONE source that states this claim. Empty if "
            f'value is "{NOT_ESTABLISHED}". Never a url you were not given.'
        ),
    )


class _DraftFinding(BaseModel):
    rights_holder: _Claim = Field(
        description=(
            f"The value must be the NAME of a specific person, organization, "
            f"or estate: nothing else. Never a sentence, a headline, or a "
            f"description of the situation. If the sources establish that "
            f"ownership is contested, diffuse, or that no single party "
            f'clearly holds the rights, write exactly "{NOT_ESTABLISHED}" '
            f"for this field rather than describing that ambiguity in "
            f"words; describing the ambiguity is what the risk_reasoning "
            f"field is for, not this one."
        )
    )
    registration_or_serial_number: _Claim
    registration_status: _Claim
    license_required: _Claim
    risk: _RISK = Field(
        description=(
            "RED: clearance required, high exposure. AMBER: likely "
            "required, unclear. GREEN: no clearance apparently needed."
        )
    )
    risk_reasoning: str
    discarded_near_misses: list[str] = Field(
        default_factory=list,
        description=(
            "Names of similar-but-different entities seen in the sources "
            "and excluded (a sibling brand, an unrelated mark sharing a "
            "word), and why. Empty list if none."
        ),
    )


_VERIFY_PROMPT = """\
You are checking clearance research for a film/TV production. Below are
search results for one entity, grouped by source. Draft a finding using
ONLY what these specific sources state.

Entity: {entity} (type: {entity_type})

Rules:
- Every claim must cite the url of the ONE source that states it. Do not
  combine information from two different sources into one claim.
- If no source states a claim, write "{not_established}" for its value and
  leave source_url empty. Do not fill gaps from your own knowledge; you
  may know things about this entity, but an uncited assertion is worthless
  in a clearance report.
- A source mentioning the entity in passing does not "establish" ownership,
  rights, or registration. Only report what the source actually says.
- rights_holder is a NAME field: the specific person, organization, or
  estate that holds the rights, and nothing else. If the sources only
  discuss who MIGHT hold rights, describe contested or unclear ownership,
  or explain the general situation without naming a specific current
  holder, write "{not_established}" for rights_holder. Do not write a
  sentence or a quoted headline into this field even if it is grounded in
  a source; a true statement that is not a name is still the wrong shape
  for this field.
- List any near-miss entities visible in the sources (sibling brands,
  unrelated marks sharing a word) that you are deliberately excluding.

Sources:
{sources_block}
"""


def _format_sources(sources: list[dict]) -> str:
    blocks = []
    for i, s in enumerate(sources, 1):
        ids = s.get("identifiers", {})
        excerpts = "\n".join(f"    - {e}" for e in s.get("excerpts", [])) or "    (no excerpts)"
        blocks.append(
            f"[{i}] {s.get('title') or '(no title)'}\n"
            f"    url: {s['url']}\n"
            f"    serial_numbers: {ids.get('serial_numbers', [])}\n"
            f"    registration_numbers: {ids.get('registration_numbers', [])}\n"
            f"    excerpts:\n{excerpts}"
        )
    return "\n\n".join(blocks)


def _grounded_in_source(value: str, source: dict, *, numeric: bool = False) -> bool:
    """Does THIS source's own excerpts/identifiers actually support `value`?

    numeric=True additionally accepts a match against the source's own
    regex-harvested serial/registration numbers (parallel_tool.py's
    _harvest_identifiers), which is a stronger check than substring-in-text
    for the highest-risk claim type: a registration number that appears in
    output but not in the harvester's own extraction is exactly the
    ambiguity that made an earlier fixed-length regex unable to tell
    "read from a source" from "invented" when a genuine registration
    number (e.g. Fanta's 7-digit 513565) fell outside the assumed length.
    """
    excerpt_blob = " ".join(source.get("excerpts", [])).lower()
    if value.lower() in excerpt_blob:
        return True
    if numeric:
        ids = source.get("identifiers", {})
        harvested = set(ids.get("serial_numbers", [])) | set(ids.get("registration_numbers", []))
        digits = re.sub(r"\D", "", value)
        if digits and digits in harvested:
            return True
    return False


def _check_claim(claim: _Claim, sources_by_url: dict, notes: list[str], field: str, *, numeric: bool = False) -> dict:
    value = claim.value.strip()
    if not value or value.lower() == NOT_ESTABLISHED:
        return {"value": NOT_ESTABLISHED, "source_url": ""}

    source = sources_by_url.get(claim.source_url)
    if source is None:
        notes.append(f"{field}: cited source_url {claim.source_url!r} is not one of this entity's sources, dropped")
        return {"value": NOT_ESTABLISHED, "source_url": ""}

    if not _grounded_in_source(value, source, numeric=numeric):
        notes.append(
            f"{field}: {value!r} not found in the excerpts of the cited source ({claim.source_url}), dropped"
        )
        return {"value": NOT_ESTABLISHED, "source_url": ""}

    return {"value": value, "source_url": claim.source_url}


def verify_finding(research_result: dict, model: str = MODEL) -> dict:
    """research_clearance() output -> a finding where every surviving claim
    is checked against the excerpts of the specific source it cites.

    Returns the same entity/entity_type/session_id/search_id envelope, plus
    rights_holder / registration_or_serial_number / registration_status /
    license_required (each {"value", "source_url"}), risk, risk_reasoning,
    discarded_near_misses, and verification_notes (claims the model drafted
    that this pass dropped, kept for audit, not shown to end users as-is).
    """
    entity = research_result.get("entity", "")
    entity_type = research_result.get("entity_type", "")
    envelope = {
        "entity": entity,
        "entity_type": entity_type,
        "session_id": research_result.get("session_id", ""),
        "search_id": research_result.get("search_id", ""),
    }

    if research_result.get("status") != "ok":
        return {
            **envelope,
            "status": "error",
            "rights_holder": {"value": NOT_ESTABLISHED, "source_url": ""},
            "registration_or_serial_number": {"value": NOT_ESTABLISHED, "source_url": ""},
            "registration_status": {"value": NOT_ESTABLISHED, "source_url": ""},
            "license_required": {"value": NOT_ESTABLISHED, "source_url": ""},
            "risk": "AMBER",
            "risk_reasoning": "Research stage failed; nothing to verify.",
            "discarded_near_misses": [],
            "verification_notes": [f"research stage error: {research_result.get('error', '')}"],
        }

    sources = research_result.get("sources", [])
    if not sources:
        return {
            **envelope,
            "status": "ok",
            "rights_holder": {"value": NOT_ESTABLISHED, "source_url": ""},
            "registration_or_serial_number": {"value": NOT_ESTABLISHED, "source_url": ""},
            "registration_status": {"value": NOT_ESTABLISHED, "source_url": ""},
            "license_required": {"value": NOT_ESTABLISHED, "source_url": ""},
            "risk": "AMBER",
            "risk_reasoning": "No sources returned by research.",
            "discarded_near_misses": [],
            "verification_notes": ["no sources to verify against"],
        }

    client = genai.Client()
    response = _generate_with_retry(
        client,
        model=model,
        contents=_VERIFY_PROMPT.format(
            entity=entity,
            entity_type=entity_type,
            not_established=NOT_ESTABLISHED,
            sources_block=_format_sources(sources),
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_DraftFinding,
            # Web excerpts being checked can themselves discuss violent,
            # explicit, or hateful subject matter (a news article about a
            # violent film, a song's slur-containing lyrics) without this
            # call itself generating any of it, see config.SAFETY_SETTINGS.
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    draft = response.parsed
    if draft is None:
        block_reason = describe_block_reason(response)
        if block_reason:
            raise ValueError(f"Gemini blocked verification ({block_reason})")
        raise ValueError("Gemini returned no parseable structured output for verification")

    sources_by_url = {s["url"]: s for s in sources}
    notes: list[str] = []

    return {
        **envelope,
        "status": "ok",
        "rights_holder": _check_claim(draft.rights_holder, sources_by_url, notes, "rights_holder"),
        "registration_or_serial_number": _check_claim(
            draft.registration_or_serial_number, sources_by_url, notes, "registration_or_serial_number", numeric=True
        ),
        "registration_status": _check_claim(draft.registration_status, sources_by_url, notes, "registration_status"),
        "license_required": _check_claim(draft.license_required, sources_by_url, notes, "license_required"),
        "risk": draft.risk,
        "risk_reasoning": draft.risk_reasoning,
        "discarded_near_misses": draft.discarded_near_misses,
        "verification_notes": notes,
    }
