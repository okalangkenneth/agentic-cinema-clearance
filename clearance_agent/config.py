"""Single source of truth for model choice and env validation.

Model notes (2026-08-08):
  gemini-2.5-flash is rejected for keys created now: "no longer available
  to new users," even though it still appears in models.list(). The list
  endpoint is broader than what a given key can call, so presence in the
  list is not proof of access.

  Pinned to gemini-3.6-flash: newest non-preview Flash. Flash rather than
  Pro because the workload is tool-calling and structured extraction across
  ~120 entities per script, not deep reasoning. Avoided -preview names
  (may vanish) and the -latest alias (may shift under us mid-build).

  Override with CLEARANCE_MODEL if a judge's key lacks access.
"""

import os

from google.genai import errors as genai_errors
from google.genai import types

MODEL = os.environ.get("CLEARANCE_MODEL", "gemini-3.6-flash")

# Screenplays contain violence, hate speech, sexual content and harassment
# as ordinary dramatic material; a scene doesn't stop needing clearance
# research because it's rough. Default Gemini safety filters operate on
# the CONTENT being processed, not on whether the caller is generating vs.
# analyzing it, so a violent action line or a slur in dialogue can trip a
# block on a call that is only asking the model to extract entity names;
# a false block here reads as "no entities found," which is
# indistinguishable from a script that genuinely has nothing to clear.
# That silent-failure shape is worse than the tool refusing outright.
#
# Verified against the installed google-genai package (not trusted from
# training data): SafetySetting(category, threshold) via
# types.HarmCategory / types.HarmBlockThreshold, passed as
# GenerateContentConfig(safety_settings=[...]). BLOCK_NONE (not OFF) is
# used deliberately: it still evaluates and annotates harm categories
# (so a genuinely disqualifying issue remains visible in the response)
# while never blocking on probability. OFF disables evaluation entirely,
# which trades away that visibility for no benefit this product needs.
#
# Only the four categories screenplay content actually exercises are
# touched. HARM_CATEGORY_CIVIC_INTEGRITY and HARM_CATEGORY_JAILBREAK are
# left at their default threshold: neither is what "violence, slurs, and
# adult content" in a script would trip, and loosening categories with no
# evidence they're the problem is scope creep on a safety-relevant setting.
SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

# Verified callable on a key created 2026-08-08, newest first.
KNOWN_GOOD_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
)


def describe_block_reason(response) -> str | None:
    """None if `response` looks like a normal completion; otherwise a
    human-readable reason, prioritizing a safety block over a generic
    "no parsed output" message.

    Used at both generate_content call sites (extract_entities,
    verify_finding) so a safety-filter block on rough screenplay content
    is diagnosed as exactly that, not reported as an opaque "Gemini
    returned nothing"; the whole point of configuring SAFETY_SETTINGS is
    that a false block should be visible and traceable, not swallowed.
    """
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        return f"prompt blocked: {block_reason}"

    candidates = getattr(response, "candidates", None) or []
    for c in candidates:
        finish_reason = getattr(c, "finish_reason", None)
        if finish_reason and str(finish_reason) not in ("STOP", "FinishReason.STOP"):
            ratings = getattr(c, "safety_ratings", None) or []
            flagged = [str(getattr(r, "category", r)) for r in ratings if getattr(r, "blocked", False)]
            detail = f", flagged categories: {flagged}" if flagged else ""
            return f"candidate finished with reason {finish_reason}{detail}"

    return None


def using_vertex() -> bool:
    """True when this process is configured to call Gemini via Vertex AI
    rather than the AI Studio API. Single source of truth for the same
    check that used to live only in run_pipeline.py's error message (which
    named "AI Studio" unconditionally, wrong on a Vertex run)."""
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE"


def platform_name() -> str:
    return "Vertex AI" if using_vertex() else "AI Studio"


def is_quota_error(exc: BaseException) -> bool:
    """True only for a 429/RESOURCE_EXHAUSTED from the Gemini API.

    Deliberately NOT a bare `except Exception`: that would also swallow a
    real bug (bad request, auth failure, malformed schema) behind the same
    retry/fallback path. This is the one narrow check shared by every
    quota-aware call site (smoke_test_report.py's Case A fallback,
    run_pipeline.py's top-level error message, and verify_finding's retry
    below) so the definition of "quota error" can't drift between them.
    """
    if not isinstance(exc, genai_errors.ClientError):
        return False
    return exc.code == 429 or (exc.status or "") == "RESOURCE_EXHAUSTED"


def require_env(*names: str) -> None:
    """Fail loudly at startup rather than cryptically mid-run.

    Judges supply their own keys; a clear message beats a stack trace.
    """
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill them in."
        )
