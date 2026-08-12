# Clearance Agent

Spike for **Agentic Cinema: The Blockbuster Hackathon** (Parallel track).

Goal: prove the plumbing works end to end. Not the product.

## What this proves (or doesn't)

1. Parallel Search API returns usable, citation-bearing results for rights-research queries
2. Parallel can be registered as an ADK function tool
3. An ADK agent runs locally and calls that tool
4. The agent deploys to Agent Engine / Agent Runtime and answers remotely

Steps 1-3 are in this repo. Step 4 comes after we confirm the ADK version story.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
pip install -r requirements.txt
copy .env.example .env          # then fill in real values
```

## Run order

**Step 0 — Parallel alone, no ADK.** Fastest way to find out if the search
results are any good for this use case. If the excerpts are useless for rights
research, the whole project idea is wrong and we want to know today.

```bash
python smoke_test_parallel.py
```

**Step 1 — ADK agent locally.**

```bash
adk run clearance_agent
# or the dev UI:
adk web
```

Ask it: `Research clearance risk for Coca-Cola, Bohemian Rhapsody, and the Nike swoosh.`

## Version check (do this first)

```bash
pip index versions google-adk
```

Docs and blogs disagree about whether the 2.x line with Workflow Runtime is
generally available. Whatever this prints determines how we do fan-out:

- **1.x only** -> use `ParallelAgent` / `SequentialAgent` workflow agents
- **2.x available** -> use Workflow Runtime (graph, fan-out/fan-in, retry)

Until that's settled, the tool fans out in plain Python. It works, it's
deterministic, and it isn't the final answer.

## Kill criteria

Stop and replan if:
- Parallel can't be cleanly wrapped as an ADK tool (auth / async / serialisation)
- Agent Runtime deploy needs org-level GCP permissions a personal account lacks
- Search excerpts don't actually surface rights-holder information
