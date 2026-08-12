"""Deterministic fan-out over script entities, as a graph.

Why this exists: with a plain Agent, the model DECIDES to call the tool once
per entity. It complied on three entities. At 120 entities under context
pressure that is a model decision, not a guarantee — and the hackathon scores
a "deterministic, multi-step agent". A graph edge cannot decide to bundle.

Verified against google-adk 2.6.3 by introspection:
    google.adk.workflow exports: BaseNode, DEFAULT_ROUTE, Edge, FunctionNode,
        JoinNode, Node, NodeTimeoutError, RetryConfig, START, Workflow
    Node fields include parallel_worker: bool, max_parallel_workers: int|None
    Node.run_node_impl(self, *, ctx: Context, node_input: Any)
        -> AsyncGenerator[Any, None]
    BaseNode.run() normalizes yields: None -> skipped, Event -> passthrough,
        anything else -> Event(output=value)
    Workflow fields: edges, max_concurrency, graph, state/input/output schema
    Edge fields: from_node, to_node, route
"""

import asyncio
import json
import uuid
from typing import Any, AsyncGenerator

from google.adk.workflow import START, FunctionNode, JoinNode, Node, Workflow

from .build_report import build_report
from .parallel_tool import research_clearance
from .script_parser import parse_and_extract
from .verify_finding import verify_finding


def _coerce_entity(node_input: Any) -> dict | None:
    """Accept whatever the runtime hands us and find the entity dict.

    The exact shape Runner delivers to a node is not documented, so rather
    than assume, we accept dict / JSON string / Content-with-text and report
    clearly when it is none of those. Tighten this once the real shape is
    confirmed empirically.
    """
    if isinstance(node_input, dict) and "entity" in node_input:
        return node_input

    text = None
    if isinstance(node_input, str):
        text = node_input
    else:
        parts = getattr(node_input, "parts", None)
        if parts:
            text = "".join(getattr(p, "text", "") or "" for p in parts)

    if text:
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict) and "entity" in parsed:
            return parsed

    return None


class ResearchNode(Node):
    """One entity in, one clearance research result out.

    parallel_worker=True means the runtime fans node_input across workers and
    calls run_node_impl once per item. The width is therefore driven by the
    data, not fixed at graph-build time — which matters because we do not know
    how many entities a screenplay holds until we have parsed it.

    max_parallel_workers caps concurrency so a 200-entity script does not open
    200 simultaneous searches.
    """

    parallel_worker: bool = True
    max_parallel_workers: int | None = 8

    async def run_node_impl(
        self, *, ctx: Any, node_input: Any
    ) -> AsyncGenerator[Any, None]:
        item = _coerce_entity(node_input)
        if item is None:
            yield {
                "status": "error",
                "error": f"unparseable node_input: {type(node_input).__name__} {node_input!r:.200}",
            }
            return

        # research_clearance is sync (the Parallel SDK call blocks). Off-thread
        # so one slow search cannot stall the other workers.
        result = await asyncio.to_thread(
            research_clearance,
            item["entity"],
            item["entity_type"],
            item.get("session_id", ""),
        )
        yield result


class VerifyNode(Node):
    """One research result in, one verified finding out.

    Chained directly after ResearchNode, inside the fan-out, rather than as
    a single pass over the collected list after JoinNode. Two reasons:

    1. Verification is per-entity — checking entity A's claims never needs
       entity B's sources. There is no cross-entity work this stage could
       do that placing it after the join would unlock.
    2. ADK's _ParallelWorker (google/adk/workflow/_parallel_worker.py)
       already fans a list across concurrent tasks and reassembles it in
       input order before handing it to the next node. Placing verification
       in its own parallel_worker node here means N verification calls run
       concurrently, the same way the N research calls already do. Placing
       it after JoinNode would hand a single Content down a single-threaded
       path, and concurrency would have to be hand-rolled with
       asyncio.gather to get back what this placement gets for free.

    The JoinNode's "no partial set" guarantee is unaffected either way: it
    still waits for every branch, it just now waits for verified findings
    instead of raw research results.

    Chaining two parallel_worker nodes back to back was not previously
    tried in this codebase — verified working here by running the workflow
    end to end (see smoke_test_verification.py).
    """

    parallel_worker: bool = True
    max_parallel_workers: int | None = 8

    async def run_node_impl(
        self, *, ctx: Any, node_input: Any
    ) -> AsyncGenerator[Any, None]:
        item = _coerce_entity(node_input)
        if item is None:
            yield {
                "status": "error",
                "error": f"unparseable node_input: {type(node_input).__name__} {node_input!r:.200}",
            }
            return

        # verify_finding calls genai.Client() synchronously. Off-thread for
        # the same reason research_clearance is: one slow call shouldn't
        # stall the other workers.
        result = await asyncio.to_thread(verify_finding, item)
        yield result


# Tie-break order for merging duplicate entities that extraction classified
# differently. Lowest index wins. "song"/"person"/"location" have dedicated
# query templates in parallel_tool.py (_QUERIES_BY_TYPE) built specifically
# to avoid the documented disambiguation failures — a trademark-shaped query
# against a song title returned NAPSTER for "Bohemian Rhapsody". Misrouting
# one of THESE three costs correctness, so they outrank the generic group.
# "brand"/"business"/"trademark" all fall through to the same
# _default_queries in parallel_tool.py, so which of the three wins doesn't
# change what gets searched — only the label shown in the eventual report.
# "trademark" is kept ahead of "brand"/"business" there because it's the
# more legally specific claim, and a downstream report reading "trademark"
# is a stronger, more actionable statement than the generic "business".
_TYPE_PRIORITY = ("song", "person", "location", "trademark", "brand", "business")


def _type_rank(entity_type: str) -> int:
    return _TYPE_PRIORITY.index(entity_type) if entity_type in _TYPE_PRIORITY else len(_TYPE_PRIORITY)


def _dedup_entities(items: list[dict]) -> list[dict]:
    """Merge work items naming the same entity, case-insensitively, keeping order.

    Extraction has produced the same real-world entity twice from one script
    (once from an all-caps scene heading, once from prose) with two different
    entity_type labels observed across runs. On a name collision the
    higher-priority entity_type (per _TYPE_PRIORITY) wins, everything else
    about the first-seen item is kept.

    Deliberately EXACT-match only (case-insensitive, whitespace-stripped).
    Near-identical names ("Nike" vs "Nike swoosh" vs "the Nike store") are
    NOT merged: fuzzy-matching risks merging two genuinely distinct entities
    into one research call, which is a worse failure than the extra cost of
    researching them separately.
    """
    merged: dict[str, dict] = {}
    for item in items:
        key = item["entity"].strip().lower()
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
        elif _type_rank(item["entity_type"]) < _type_rank(existing["entity_type"]):
            merged[key] = item
    return list(merged.values())


def prepare_entities(entities: list | None = None, script_path: str = "") -> list[dict]:
    """Emit one work item per entity, for the research node to fan over.

    Exists because parallel_worker fans over a LIST produced upstream. Without
    this node the research node received the whole request once instead of
    once per entity — confirmed empirically.

    Bound to session state by parameter name, so `entities` and `script_path`
    are filled from state["entities"] / state["script_path"]. Falls back to
    script_path when entities isn't supplied, so a screenplay can be handed
    to the workflow directly instead of pre-extracted entities. Verified
    against the installed FunctionNode source
    (google/adk/workflow/_function_node.py, _bind_parameters): when a bound
    parameter name is absent from session state, the function's default is
    used rather than raising, so script_path="" is safe here.
    """
    if not entities and script_path:
        entities = parse_and_extract(script_path)
    if not entities:
        return []
    valid = [e for e in entities if isinstance(e, dict) and "entity" in e]
    valid = _dedup_entities(valid)
    session_id = f"script-{uuid.uuid4().hex[:8]}"
    for e in valid:
        e.setdefault("session_id", session_id)
    return valid


def _build_report_node(node_input: Any) -> str:
    """Wraps build_report.build_report for the FunctionNode wiring below.

    Initially wired this with parameter_binding="node_input", on the
    (wrong) assumption that a parameter literally named node_input gets the
    raw payload directly under that mode. Actually verified empirically
    (a standalone Runner run against this exact node, no LLM calls needed)
    that it is the OPPOSITE: parameter_binding="node_input" mode looks
    `param_name` up AS A KEY inside the payload, same as any other bound
    parameter name. The raw-payload passthrough for a
    parameter named node_input only fires in parameter_binding="state"
    mode (see _function_node.py's _bind_parameters docstring: "In 'state'
    mode, the node_input parameter is passed through directly"). So this
    node is wired below with parameter_binding="state", not "node_input" —
    despite the parameter being named node_input. That payload, downstream
    of a JoinNode, is a dict keyed by the upstream node's name (here
    {"verify_findings": [...]}) — build_report() already accepts that
    shape without assuming the key, since the key is just verify_findings'
    node name and shouldn't be load-bearing here.
    """
    return build_report(node_input)


def build_workflow(max_concurrency: int = 8) -> Workflow:
    """START -> prepare -> research (fanned) -> verify (fanned) -> join -> report.

    The join waits for every verify worker before anything downstream runs,
    so a report can never be built from a partial or unverified entity set.
    """
    prepare = FunctionNode(
        name="prepare_entities",
        func=prepare_entities,
        # 'state' binds function parameters by NAME from session state, so
        # `entities` here is filled from state["entities"]. ('node_input'
        # binds by name from the incoming payload — it does not hand you the
        # payload itself, which is what the first attempt got wrong.)
        parameter_binding="state",
    )
    research = ResearchNode(
        name="research_entities",
        description="Research rights clearance for each script entity.",
        max_parallel_workers=max_concurrency,
    )
    verify = VerifyNode(
        name="verify_findings",
        description="Drop any claim not supported by the source it cites.",
        max_parallel_workers=max_concurrency,
    )
    collect = JoinNode(
        name="collect_findings",
        description="Wait for every entity before reporting.",
    )
    report = FunctionNode(
        name="build_report",
        func=_build_report_node,
        # 'state' binding, NOT 'node_input' binding — despite the function's
        # parameter being named node_input. See _build_report_node's
        # docstring: in 'state' mode a parameter literally named node_input
        # is special-cased to receive the raw incoming payload directly;
        # 'node_input' MODE does the opposite; it looks param names up as
        # keys inside the payload, which is wrong here since the payload is
        # a bare list/dict, not something with a "node_input" key.
        parameter_binding="state",
    )

    return Workflow(
        name="clearance_workflow",
        description="Deterministic per-entity clearance research over a script.",
        edges=[(START, prepare, research, verify, collect, report)],
        max_concurrency=max_concurrency,
    )


def make_entity_batch(entities: list[tuple[str, str]]) -> list[dict]:
    """Shape (name, type) pairs into node inputs sharing one session id."""
    session_id = f"script-{uuid.uuid4().hex[:8]}"
    return [
        {"entity": name, "entity_type": kind, "session_id": session_id}
        for name, kind in entities
    ]
