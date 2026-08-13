# Clearance Agent

Reads a screenplay and tells a production what it needs to clear before it
can shoot: who currently owns each brand, song, trademark, likeness and
location that appears on the page, and how risky each one is.

Built for **Agentic Cinema: The Blockbuster Hackathon** (Parallel track).

## The problem

Every real-world thing in a screenplay is a rights question. A can of Coke
on a table, a song on a jukebox, a real bar the characters walk into, a
celebrity mentioned by name: each one needs clearing before the camera
rolls, and getting it wrong means reshoots, a delayed release, or a suit.

Productions send scripts to clearance houses and pay thousands of dollars
to wait one to three weeks for a report. The research itself is mechanical:
find the entity, find who owns it now, find the registration, cite the
source. It is slow because it is done by hand.

This agent does that research pass and returns a cited, risk-scored report.
It does not replace production counsel. It gives counsel a head start with
the sources already attached.

## What it produces

For every entity found in the script:

- **Current rights holder**, not the historical one. Catalogues change
  hands; the answer that matters is who owns it today.
- **Registration or serial number**, where one exists.
- **Registration status** and **what licence would be required**.
- **A RED / AMBER / GREEN risk rating** with the reasoning stated.
- **A source URL for every single claim.**
- **Near-misses that were considered and discarded**, and why. A
  same-named entity in a different class is the classic clearance trap.
- **Near-identical mentions of the same clearance subject grouped into one
  row** ("Nike", "NIKE OUTLET STORE" and "Nike swoosh" as one entry, not
  three), while every surface form stays individually researched, verified
  and fully visible in the detail section below it.

Anything the sources do not establish is labelled **"not established by
sources"** rather than left blank or filled in from the model's own
memory. A reader can always tell the difference between *checked and found
nothing* and *not checked*.

## Why the citations are the product

A research tool that invents a rights holder is worse than no tool at all,
because it produces confident text a production might act on.

So findings are not trusted on the model's say-so. After research, a
verification stage checks every drafted claim **against the specific source
it cites**, not against a pool of all sources for that entity. If a claim's
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
reach the report. A graph edge cannot decide to bundle entities the way a
model deciding to loop over a tool call can. `run_pipeline.py` (below) drives
this exact graph through a real ADK `Runner`, on the shipped sample script.

Measured on 9 entities (`examples/sample_script.fdx`, full pipeline): after
extraction (~13-19s), research and verification, the fanned stages, took
65.5s running concurrently versus 184.0s for the identical 9 entities
through the same graph run sequentially (`max_concurrency=1`). That is a
~2.8x speedup. This supersedes an earlier 3-entity, research-only figure,
since this run covers more entities and both fanned stages on the same
graph. Total wall-clock, including the near-identical-name grouping call
described below: 90.9s on a real Vertex run against the shipped sample
script, entity count unchanged from the measurement above (9), so the
concurrent-vs-sequential comparison itself was not re-run.

`adk run clearance_agent` talks to a simpler `Agent` wrapping the same
`research_clearance` tool instead, where the model itself decides to call the
tool once per entity. That is useful for interactive, ad-hoc questions, but
it is not the deterministic graph.

## Stack

| Component | Used for |
|---|---|
| Google Agent Development Kit (ADK) 2.x | Workflow graph, concurrent fan-out/fan-in |
| Gemini | Entity extraction, claim drafting and near-identical-name grouping, all with structured output |
| Vertex AI Agent Engine | Hosting |
| Parallel Search API | Live rights research, the load-bearing search layer |
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
access: either an AI Studio key for local runs, or a Google Cloud project
with Vertex AI enabled.

## Running it

**The full pipeline, script in to report out.** This is the deterministic
graph described above, actually running:

```bash
python run_pipeline.py examples/sample_script.fdx
```

Prints each entity as its research and verification finish, out of input
order, because that is the visible evidence the fan-out is real rather than
a model looping over a tool call. It then writes a report HTML file and
prints its path. `examples/sample_script.fdx` ships in this repo, so this
runs on a fresh clone with no setup beyond `.env`.

**Want the quick one?** `examples/demo_script.fdx` is a short 3-scene
screenplay built to show grouping, a real registration number with a
discarded near-miss, and a `not established by sources` case in one run,
without the full sample's entity count:

```bash
python run_pipeline.py examples/demo_script.fdx
```

Measured on Vertex, 5 back-to-back runs: 66.7s-83.5s internal wall clock
(all passed; no failures). That is slower than the roughly 40-50s originally
targeted for an uncut recording; cutting further would mean dropping one of
the three things this file exists to show on screen, so the time was left
as measured rather than the content cut to hit the number. `examples/
sample_script.fdx` stays the fuller example, unchanged.

**On a free-tier AI Studio key, this will likely 429.** The concurrent
verification fan-out sends several `generate_content` calls within the same
few seconds, and the free tier caps `gemini-3.6-flash` at 5 requests/minute,
a limit discovered running this exact command and separate from the
already-documented 20/day cap. `run_pipeline.py` reports this clearly rather
than as a raw traceback, though the ADK runtime itself also logs one to
stderr and that part is not suppressible from here. The measurement above
was taken against Vertex (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`,
`CLEARANCE_MODEL=gemini-2.5-flash`), which has real headroom. A paid AI
Studio key would too.

**Search quality on its own**, before any agent machinery. The fastest way
to see whether the research layer actually returns rights-bearing results:

```bash
python smoke_test_parallel.py
```

**The agent locally, interactively.** The model decides tool calls here, so
this is not the deterministic graph (see the caveat above):

```bash
adk run clearance_agent
```

Then ask it, for example:
`Research clearance risk for Coca-Cola, Bohemian Rhapsody, and the Nike swoosh.`

**Individual pipeline stages, exercised directly.** Useful when developing a
single stage in isolation, each proven against real API calls:

```bash
python smoke_test_script_parsing.py    # requires a local .fdx fixture, not shipped
python smoke_test_verification.py
python smoke_test_report.py            # writes HTML output to scratch/
```

`smoke_test_script_parsing.py` reads a fixture at `scratch/test_script.fdx`,
which is gitignored dev material rather than `examples/sample_script.fdx`.
Point `FIXTURE` at the shipped sample (or your own `.fdx`) to run it on a
fresh clone.

**Deployed:**

```bash
python deploy.py          # deploys to Vertex AI Agent Engine
python query_remote.py    # queries the deployed agent
```

The deployed agent currently runs the same simple `Agent` as `adk run`
above, not the `Workflow` graph. Deploying the graph instead is a real open
question, not addressed here.

## Notes on scope

The entity taxonomy covers brands, businesses, trademarks, songs, people
and locations. Musical groups currently classify as `person`, a known gap.

Extraction is not fully deterministic between runs: the same unambiguous
entity can occasionally be typed differently. This affects the label and
which query template routes the search, not whether the entity is found.

Deduplication before research merges exact name matches only. Near-identical
names ("Nike", "NIKE OUTLET STORE", "Nike swoosh") are deliberately left
unmerged at that stage, because wrongly merging two distinct real-world
entities into one research call loses a clearance risk, and that is the
failure this tool exists to prevent: a brand and an unrelated place sharing
a word must never be folded into one search.

Instead, grouping happens after research and verification, in the report
itself: a small Gemini call reasons about which already-independently-
verified entities describe one real-world clearance subject, and the report
shows them as one row with every surface form still listed and individually
traceable to its own full, independently-checked detail section. If that
call fails, is blocked, or returns anything that does not account for every
input name exactly once, the report falls back to one row per entity
(today's prior behaviour) rather than guessing; no entity that appears in
the script can silently disappear from the report as a result of grouping.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
