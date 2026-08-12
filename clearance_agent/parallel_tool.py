"""Parallel Search wrapped as an ADK function tool.

API surface verified against parallel-web 1.1.0 by introspection.
If you upgrade the SDK, re-verify before trusting this.

    client.search(
        *,                                  # keyword-only
        search_queries: list[str],          # REQUIRED. 3-6 words each, 2-3 of them
        objective: str | None,
        mode: 'turbo' | 'basic' | 'advanced' = 'advanced',
        max_chars_total, session_id, client_model,
        advanced_settings = {
            max_results,
            source_policy: {include_domains, exclude_domains, after_date},
            excerpt_settings: {max_chars_per_result},
            fetch_policy: {max_age_seconds, timeout_seconds, disable_cache_fallback},
        },
    ) -> SearchResult

    SearchResult:    results, search_id, session_id, usage, warnings
    WebSearchResult: url, title, publish_date, excerpts (a LIST of str)

DESIGN NOTE: why there is no include_domains here.
A smoke test on 2026-08-08 compared registry-only filtering against
unfiltered search. Filtering made results markedly WORSE.
Official registries are query-driven databases, not crawlable documents:
TSDR needs a serial number in the URL, so crawling it returns the homepage
help text. The entity-level records that ARE indexable live on aggregators
(Justia Trademarks, uspto.report, Trademarkia, easysong), which mirror
registry data as static pages.

So: discovery runs unfiltered and harvests the SERIAL/REGISTRATION NUMBER,
which is then the key to the authoritative record in stage two.
"""

import os
import re

from parallel import Parallel

from .config import MODEL

# Aggregators that mirror registry data as crawlable, entity-level pages.
# Kept for reference and for scoring source authority downstream. NOT used
# as an include_domains filter. See DESIGN NOTE above.
REGISTRY_MIRRORS = (
    "trademarks.justia.com",
    "uspto.report",
    "trademarkia.com",
    "trademarkelite.com",
    "easysong.com",
    "sec.gov",
)

# Q&A and forum sites: opinions about the law, not records of it.
EXCLUDED_DOMAINS = [
    "avvo.com",
    "quora.com",
    "reddit.com",
    "answers.com",
]

# USPTO serial numbers are 8 digits. Registration numbers are NOT fixed at 7:
# older marks are shorter (Fanta, registered 1949, is 513565) and sources
# zero-pad inconsistently. A 7-digit-only pattern silently dropped these:
# found when the agent reported a registration number our harvester had not
# captured, leaving us unable to tell whether it was read or invented.
_SERIAL_RE = re.compile(r"[Ss]erial\D{0,20}(\d{8})")
_REGNO_RE = re.compile(r"[Rr]egistration\s*[Nn]umber\D{0,10}(\d{4,8})")


def _harvest_identifiers(excerpts: list[str]) -> dict:
    """Pull registry identifiers out of ONE source's excerpts.

    Deliberately per-source, never pooled across sources. Pooling destroys
    provenance: the v2 smoke run harvested NAPSTER's serial (76137325, owned
    by Rhapsody International) while researching the song Bohemian Rhapsody.
    Attributing that serial to a Queen song is precisely the failure this
    product exists to prevent. An identifier is only meaningful attached to
    the source that stated it.
    """
    blob = " ".join(excerpts)
    serials = sorted(set(_SERIAL_RE.findall(blob)))
    regnos = sorted(set(_REGNO_RE.findall(blob)))
    return {
        "serial_numbers": serials,
        "registration_numbers": regnos,
        "tsdr_urls": [
            f"https://tsdr.uspto.gov/#caseNumber={s}&caseType=SERIAL_NO&searchType=statusSearch"
            for s in serials
        ],
    }


# Songs are copyright, not trademark. Asking a trademark-shaped question about
# a song drags the search into trademark registries, which keyword-match the
# title: the v2 run returned RHAPSODY (Rhapsody International), NAPSTER and
# RHAPSODY (Telkonet) for "Bohemian Rhapsody". Query strategy must branch.
_QUERIES_BY_TYPE = {
    "song": lambda e: [
        f"{e} song copyright publisher",
        f"{e} music licensing sync rights",
        f"{e} performing rights ASCAP BMI",
    ],
    "person": lambda e: [
        f"{e} likeness rights representation",
        f"{e} publicity rights estate",
        f"{e} depiction clearance permission",
    ],
    "location": lambda e: [
        f"{e} filming permit location",
        f"{e} property release filming",
        f"{e} trademark trade dress",
    ],
}


def _default_queries(entity: str) -> list[str]:
    """Trademark-shaped queries: brands, businesses, trademarks."""
    return [
        f"{entity} trademark registration serial number",
        f"{entity} rights holder owner",
        f"{entity} film licensing clearance",
    ]


_OBJECTIVE_BY_TYPE = {
    "song": (
        "Identify the copyright owner and music publisher for the song '{e}'. "
        "Establish who controls the composition and who controls the master "
        "recording, which performing rights organisation represents them, and "
        "what synchronisation and master use licences are required to use it "
        "in a film or television production. This is a musical work, not a "
        "trademark; ignore trademark registrations for similar words."
    ),
    "person": (
        "Identify who controls the likeness, name and publicity rights for "
        "'{e}', including any estate or representative, and what permission "
        "is required to depict them in a film or television production."
    ),
}

_OBJECTIVE_DEFAULT = (
    "Identify the current rights holder for the {t} '{e}'. Establish the exact "
    "registered owner name, the trademark serial or registration number, "
    "whether the registration is currently active, and what licence is "
    "required to depict it in a film or television production. Report only "
    "records for this exact entity; ignore similarly named marks belonging "
    "to other owners."
)



def research_clearance(entity: str, entity_type: str, session_id: str = "") -> dict:
    """Research who holds the rights to a real-world entity appearing in a script.

    Use for any brand, song, real person, business name, trademark or location
    that appears in a screenplay and would need legal clearance before filming.
    Call once per entity. Never bundle several entities into one call.

    Args:
        entity: Name as it appears in the script, e.g. "Coca-Cola",
            "Bohemian Rhapsody", "Nike swoosh".
        entity_type: One of "brand", "song", "person", "business",
            "trademark", "location".
        session_id: Optional. Pass the same value for every entity in one
            script so the searches are related as a single task.

    Returns:
        status: "ok" or "error"
        entity, entity_type: echoed back
        sources: list of sources, each with url, title, publish_date,
            excerpts, is_registry_mirror, and its OWN identifiers
            (serial_numbers, registration_numbers, tsdr_urls). Identifiers
            belong to the source that stated them; never combine them
            across sources or attach one to an entity the source is not
            clearly about.
        search_id, session_id: trace identifiers, kept for audit
        warnings: any warnings returned by the search
        error: present only when status is "error"
    """
    if not os.environ.get("PARALLEL_API_KEY"):
        return {"status": "error", "entity": entity, "error": "PARALLEL_API_KEY not set"}

    client = Parallel()

    objective_tpl = _OBJECTIVE_BY_TYPE.get(entity_type, _OBJECTIVE_DEFAULT)
    objective = objective_tpl.format(e=entity, t=entity_type)

    query_fn = _QUERIES_BY_TYPE.get(entity_type)
    queries = query_fn(entity) if query_fn else _default_queries(entity)

    kwargs = {
        # 3-6 words each; longer queries degrade result quality.
        "search_queries": queries,
        "objective": objective,
        "mode": "advanced",
        "client_model": MODEL,
        "advanced_settings": {
            "max_results": 8,
            # No include_domains, see DESIGN NOTE. Excluding forums only.
            "source_policy": {"exclude_domains": EXCLUDED_DOMAINS},
            "excerpt_settings": {"max_chars_per_result": 2000},
        },
    }
    if session_id:
        kwargs["session_id"] = session_id

    try:
        result = client.search(**kwargs)
    except Exception as exc:  # noqa: BLE001 - surface failure to the agent
        return {"status": "error", "entity": entity, "error": f"{type(exc).__name__}: {exc}"}

    sources = []
    for r in result.results:
        excerpts = list(r.excerpts or [])
        sources.append(
            {
                "url": r.url,
                "title": r.title or "",
                "publish_date": r.publish_date or "",
                "excerpts": excerpts,
                "is_registry_mirror": any(d in (r.url or "") for d in REGISTRY_MIRRORS),
                # Per-source, never pooled. See _harvest_identifiers docstring.
                "identifiers": _harvest_identifiers(excerpts),
            }
        )

    return {
        "status": "ok",
        "entity": entity,
        "entity_type": entity_type,
        "search_id": result.search_id,
        "session_id": result.session_id,
        "warnings": [str(w) for w in (result.warnings or [])],
        "sources": sources,
    }
