# Clearance Agent

Reads a screenplay and tells a production what it needs to clear before it
can shoot — who currently owns each brand, song, trademark, likeness and
location that appears on the page, and how risky each one is.

Built for **Agentic Cinema: The Blockbuster Hackathon** (Parallel track).

## The problem

Every real-world thing in a screenplay is a rights question. A can of Coke
on a table, a song on a jukebox, a real bar the characters walk into, a
celebrity mentioned by name — each one needs clearing before the camera
rolls, and getting it wrong means reshoots, a delayed release, or a suit.

Productions send scripts to clearance houses and pay thousands of dollars
to wait one to three weeks for a report. The research itself is mechanical:
find the entity, find who owns it now, find the registration, cite the
source. It is slow because it is done by hand.

This agent does that research pass and returns a cited, risk-scored report.
It does not replace production counsel — it gives counsel a head start with
the sources already attached.

## What it produces

For every entity found in the script:

- **Current rights holder** — not the historical one. Catalogues change
  hands; the answer that matters is who owns it today.
- **Registration or serial number**, where one exists.
- **Registration status** and **what licence would be required**.
- **A RED / AMBER / GREEN risk rating** with the reasoning stated.
- **A source URL for every single claim.**
- **Near-misses that were considered and discarded**, and why — a
  same-named entity in a different class is the classic clearance trap.

Anything the sources do not establish is labelled **"not established by
sources"** rather than left blank or filled in from the model's own
memory. A reader can always tell the difference between *checked and found
nothing* and *not checked*.

## Why the citations are the product

A research tool that invents a rights holder is worse than no tool at all,
because it produces confident text a production might act on.

So findings are not trusted on the model's say-so. After research, a
verification stage checks every drafted claim **against the specific source
it cites** — not against a pool of all sources for that entity. If a claim's
value does not actually appear in that one source's own excerpts, the claim
does not survive. This is a programmatic check, not a second request to the
model to be more careful.

Per-source provenance is enforced at harvest time too: an identifier is
bound to the source that stated it, never pooled across sources. Two
sources talking about different things cannot combine into one confident
wrong answer.

## How it works

```
  screenplay (.fdx)
        |
  [ parse ]        Final Draft XML -> scene headings, action, dialogue
        |
  [ extract ]      Gemini structured output -> typed entities
        |
  [ dedup ]        one entity, one research job
        |
  [ research ]     Parallel Search API, concurrent fan-out  <- one per entity
        |
  [ verify ]       every claim checked against its own cited source
        |
  [ report ]       risk-ordered HTML with full citation trail
```

Every stage above is implemented as a `google.adk.Workflow` graph
(`clearance_agent/workflow.py`): a `FunctionNode` handles parse/extract/dedup,
two chained `parallel_worker` nodes fan research and verification out across
entities concurrently, and a `JoinNode` guarantees no partial entity set can
reach the report — a graph edge cannot decide to bundle entities the way a
model deciding to loop over a tool call can. Measured on a 3-entity run: 5.4s
concurrent versus ~15s sequential, completing out of input order.

`adk run clearance_agent` (below) talks to a simpler `Agent` wrapping the
same `research_clearance` tool, where the model itself decides to call the
tool once per entity — useful for interactive, ad-hoc questions, but not the
deterministic graph. The graph is exercised directly via its component
functions in the pipeline smoke tests; there is no single shipped command
that drives a full script through the graph via `Runner` end to end.

## Stack

| Component | Used for |
|---|---|
| Google Agent Development Kit (ADK) 2.x | Workflow graph, concurrent fan-out/fan-in |
| Gemini | Entity extraction and claim drafting, both with structured output |
| Vertex AI Agent Engine | Hosting |
| Parallel Search API | Live rights research — the load-bearing search layer |
| Google Secret Manager | API key storage for the deployed agent |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (cmd/PowerShell)
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env             # then fill in real values (Windows: `copy` instead of `cp`)
```

You need a Parallel API key (https://platform.parallel.ai) and Gemini
access — either an AI Studio key for local runs, or a Google Cloud project
with Vertex AI enabled.

## Running it

**Search quality on its own**, before any agent machinery — the fastest way
to see whether the research layer actually returns rights-bearing results:

```bash
python smoke_test_parallel.py
```

**The agent locally, interactively** (model decides tool calls — see the
caveat above, this is not the deterministic graph):

```bash
adk run clearance_agent
```

Then ask it, for example:
`Research clearance risk for Coca-Cola, Bohemian Rhapsody, and the Nike swoosh.`

**The pipeline stages, exercised directly** — parsing, extraction, dedup,
research, verification, and report rendering, each proven against real
API calls:

```bash
python smoke_test_script_parsing.py    # requires a local .fdx fixture — see note below
python smoke_test_verification.py
python smoke_test_report.py            # writes HTML output to scratch/
```

`smoke_test_script_parsing.py` reads a fixture at `scratch/test_script.fdx`.
`scratch/` is gitignored and not shipped in this repo, so this script will
not run out of the box on a fresh clone — point `FIXTURE` at your own `.fdx`
screenplay to exercise it, or read the file to see what it checks for.

**Deployed:**

```bash
python deploy.py          # deploys to Vertex AI Agent Engine
python query_remote.py    # queries the deployed agent
```

## Notes on scope

The entity taxonomy covers brands, businesses, trademarks, songs, people
and locations. Musical groups currently classify as `person` — a known gap.

Extraction is not fully deterministic between runs: the same unambiguous
entity can occasionally be typed differently. This affects the label and
which query template routes the search, not whether the entity is found.
Deduplication merges exact name matches only; near-identical names are
deliberately left unmerged, because wrongly merging two distinct
real-world entities loses a clearance risk, and that is the failure this
tool exists to prevent.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
