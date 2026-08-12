"""Drive the deterministic Workflow graph end to end: a script path in, a
rendered HTML clearance report out.

    python run_pipeline.py examples/sample_script.fdx

This is the difference between "the graph exists in workflow.py" and "the
graph is what actually runs": `adk run clearance_agent` talks to the
simpler, model-decides-the-fan-out `Agent` in agent.py instead. This script
drives `build_workflow()` through a real `Runner`:
`Runner(node=..., session_service=..., auto_create_session=True)` plus
`run_async(state_delta={...})` to seed FunctionNode parameters bound by
name from session state, the pattern already verified working for this
graph's individual nodes elsewhere in this codebase, exercised here for
the first time as a single end-to-end run.

Runner/Event API surface verified against the installed google-adk 2.6.3 by
introspection before writing against it:
    Runner(*, node, session_service, auto_create_session=True)
    Runner.run_async(*, user_id, session_id, new_message, state_delta)
        -> AsyncGenerator[Event, None]
    Event fields include: output (the node's yielded value, or None),
        node_info.path (e.g. "research_entities@1/research_entities@1" for
        a parallel_worker item; the repeated segment is a known quirk, not
        a bug), author, branch.
A worker exception inside a parallel_worker node (confirmed by reading
google/adk/workflow/_parallel_worker.py) cancels its siblings and re-raises
out of the node, which surfaces as an exception from this script's
`async for` loop rather than as an error Event, so failures are caught
around the loop, not inside it.
"""

import argparse
import asyncio
import os
import sys
import time
import uuid

from dotenv import load_dotenv

# Web/script text can contain characters cp1252 can't encode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from google.adk import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402
from google.genai import types  # noqa: E402

from clearance_agent.workflow import build_workflow  # noqa: E402


def _is_quota_error(exc: Exception) -> bool:
    """A real 429/RESOURCE_EXHAUSTED from Gemini, not a bug to swallow.

    Same check as smoke_test_report.py's _is_quota_error, for the same
    reason: a bare `except Exception` here would also hide a real bug in
    the pipeline behind a "just quota" message.
    """
    if not isinstance(exc, genai_errors.ClientError):
        return False
    return exc.code == 429 or (exc.status or "") == "RESOURCE_EXHAUSTED"


def _default_output_path(script_path: str) -> str:
    stem = os.path.splitext(os.path.basename(script_path))[0]
    return f"{stem}_clearance_report.html"


def _node_base_name(path: str) -> str:
    """The node this event belongs to, stripped of its '@N' instance suffix.

    node_info.path is prefixed with the WORKFLOW's own name too (e.g.
    "clearance_workflow@1/research_entities@1/research_entities@1" for a
    parallel_worker item's per-item event, or ".../research_entities@1"
    without the repeat for that node's aggregate list event); the node's
    own name is always the LAST segment, not the first. Taking the first
    segment silently matched nothing (every path's first segment is the
    workflow's own name), caught in a synthetic dry run before this cost
    any live API quota.
    """
    return path.rstrip("/").split("/")[-1].split("@")[0]


async def _run(script_path: str, output_path: str, max_concurrency: int) -> None:
    workflow = build_workflow(max_concurrency=max_concurrency)
    runner = Runner(
        node=workflow,
        session_service=InMemorySessionService(),
        auto_create_session=True,
    )

    session_id = f"pipeline-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    entity_total = None
    verified_count = 0
    html_report = None

    def elapsed() -> str:
        return f"[{time.monotonic() - started:5.1f}s]"

    print(f"Running clearance pipeline on {script_path}")
    print(f"{elapsed()} parsing + extracting + deduplicating entities...")

    async for event in runner.run_async(
        user_id="pipeline",
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text="run")]),
        state_delta={"script_path": script_path},
    ):
        output = event.output
        if output is None:
            continue
        node = _node_base_name(event.node_info.path) if event.node_info else ""

        if node == "prepare_entities" and isinstance(output, list):
            entity_total = len(output)
            print(f"{elapsed()} {entity_total} entities after dedup, researching concurrently...")
        elif node == "research_entities" and isinstance(output, dict) and "entity" in output:
            status = "ok" if output.get("status") == "ok" else f"ERROR: {output.get('error')}"
            print(f"{elapsed()}   research done  -> {output['entity']}  ({status})")
        elif node == "verify_findings" and isinstance(output, dict) and "entity" in output:
            verified_count += 1
            tag = output.get("risk") or output.get("status")
            total = f"/{entity_total}" if entity_total else ""
            print(f"{elapsed()}   verified {verified_count}{total} -> {output['entity']}  [{tag}]")
        elif node == "build_report" and isinstance(output, str):
            html_report = output

    total_elapsed = time.monotonic() - started

    if html_report is None:
        raise RuntimeError(
            "pipeline finished without producing a report: no build_report "
            "event was seen; check the progress lines above for where it stopped"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_report)

    print()
    print(f"done in {total_elapsed:.1f}s, {verified_count} entities verified")
    print(f"report written to: {os.path.abspath(output_path)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the clearance research pipeline on a screenplay.")
    ap.add_argument("script_path", help="path to a .fdx screenplay")
    ap.add_argument("-o", "--output", help="output HTML path (default: <script>_clearance_report.html)")
    ap.add_argument(
        "--max-concurrency", type=int, default=8, help="max concurrent research/verify workers (default: 8)"
    )
    args = ap.parse_args()

    if not os.path.isfile(args.script_path):
        raise SystemExit(f"error: script not found: {args.script_path}")

    if not os.environ.get("PARALLEL_API_KEY"):
        raise SystemExit("error: PARALLEL_API_KEY not set. Copy .env.example to .env and fill it in.")

    using_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE"
    if not using_vertex and not os.environ.get("GOOGLE_API_KEY"):
        raise SystemExit(
            "error: GOOGLE_API_KEY not set (or set GOOGLE_GENAI_USE_VERTEXAI=TRUE with a "
            "GOOGLE_CLOUD_PROJECT for Vertex). Copy .env.example to .env and fill it in."
        )

    output_path = args.output or _default_output_path(args.script_path)

    try:
        asyncio.run(_run(args.script_path, output_path, args.max_concurrency))
    except Exception as exc:
        if _is_quota_error(exc):
            raise SystemExit(
                "error: Gemini quota exhausted (429 RESOURCE_EXHAUSTED). "
                "AI Studio's free tier caps this model at both 20 requests/day and "
                "5 requests/minute -- the concurrent verification fan-out this pipeline "
                "runs is likely to hit the per-minute cap on its own. Try again in a "
                "minute, tomorrow if the daily cap is what you hit, or set "
                "GOOGLE_GENAI_USE_VERTEXAI=TRUE with a Vertex-enabled project for headroom."
            )
        raise SystemExit(f"error: pipeline failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
