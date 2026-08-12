"""Script parsing: .fdx -> plain text -> extracted entities.

FDX is Final Draft's XML format: <FinalDraft><Content><Paragraph Type="...">
<Text>...</Text></Paragraph>...</Content></FinalDraft>. No sample .fdx shipped
with this repo, so parse_fdx is built and tested against a synthetic fixture
(scratch/, gitignored) rather than trusted from memory. A Paragraph can hold
several <Text> runs (one per formatting change mid-line, e.g. a bolded word),
so text must be joined across ALL Text children of a paragraph, not just the
first; a single-run read would silently truncate any line with inline
formatting.

Verified against the installed SDK by introspection (2026-08-11):
    google.genai.Client() reads GOOGLE_API_KEY / GOOGLE_GENAI_USE_VERTEXAI
        from env automatically, matching this project's .env, no wiring needed.
    types.GenerateContentConfig has response_mime_type + response_schema for
        structured output; a pydantic BaseModel is an accepted schema type.
    GenerateContentResponse.parsed returns the schema instance directly.
"""

import xml.etree.ElementTree as ET
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import MODEL, SAFETY_SETTINGS, describe_block_reason

# Paragraph types that can carry a real-world entity worth clearance
# research. "Character" (the cast list name, e.g. "JOHN") is deliberately
# excluded: it's a role, not a real-world entity to clear.
_RELEVANT_TYPES = {"Scene Heading", "Action", "Dialogue"}


def parse_fdx(path: str) -> str:
    """Extract plain script text from a Final Draft .fdx file.

    One line per paragraph, prefixed with its type, so the extraction model
    gets scene-heading/action/dialogue context without needing the full FDX
    tree.

    Raises ValueError if the file isn't a script FDX or has no usable text,
    rather than returning silently empty output that would later look like
    "the script has no clearance issues" instead of "parsing failed".
    """
    tree = ET.parse(path)
    root = tree.getroot()
    content = root.find("Content")
    if content is None:
        raise ValueError(f"{path}: no <Content> element, not a script FDX")

    lines = []
    for para in content.findall("Paragraph"):
        ptype = para.get("Type", "")
        if ptype not in _RELEVANT_TYPES:
            continue
        text = "".join(t.text or "" for t in para.findall("Text")).strip()
        if text:
            lines.append(f"[{ptype}] {text}")

    if not lines:
        raise ValueError(
            f"{path}: parsed but found no Scene Heading/Action/Dialogue text"
        )

    return "\n".join(lines)


_ENTITY_TYPE = Literal["brand", "business", "trademark", "song", "person", "location"]


class ExtractedEntity(BaseModel):
    entity: str = Field(description="Name exactly as it appears in the script")
    entity_type: _ENTITY_TYPE
    context: str = Field(
        description="The line or short phrase the entity appeared in, for a human reviewing extraction quality"
    )


class ExtractedEntities(BaseModel):
    entities: list[ExtractedEntity]


EXTRACTION_PROMPT = """\
You are reading a screenplay to find every real-world entity that would
need legal clearance before the production could film it: brands,
businesses, trademarks, songs, real people, and real filming locations.

Rules:
- Only extract entities that are REAL and would need rights clearance.
  Fictional characters, invented company names, and made-up song titles
  do not need clearance; skip them.
- Use the exact name as it appears in the script.
- Classify each as one of: brand, business, trademark, song, person, location.
- If you are unsure whether something is real or fictional, include it. A
  clearance researcher can rule it out later; missing a real entity is the
  worse failure.
- Do not invent entities that are not present in the text.

Screenplay text follows, one line per paragraph with its type in brackets:

{script_text}
"""


def extract_entities(script_text: str, model: str = MODEL) -> list[dict]:
    """Gemini structured extraction: script text -> typed entity list.

    Returns the shape workflow.prepare_entities already validates:
    [{"entity": ..., "entity_type": ...}, ...]. `context` is dropped here;
    it exists for a human reviewing extraction quality, not for the research
    tool, which only takes entity/entity_type/session_id.
    """
    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=EXTRACTION_PROMPT.format(script_text=script_text),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedEntities,
            # Screenplay text is ordinary dramatic material, not a request
            # to generate harmful content, see config.SAFETY_SETTINGS.
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        block_reason = describe_block_reason(response)
        if block_reason:
            raise ValueError(f"Gemini blocked entity extraction ({block_reason})")
        raise ValueError(
            "Gemini returned no parseable structured output for entity extraction"
        )

    return [{"entity": e.entity, "entity_type": e.entity_type} for e in parsed.entities]


def parse_and_extract(path: str, model: str = MODEL) -> list[dict]:
    """.fdx path -> entity work items, in one call. The prepare_entities seam."""
    script_text = parse_fdx(path)
    return extract_entities(script_text, model=model)
