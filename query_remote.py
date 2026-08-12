"""Query the DEPLOYED agent. Proves the container actually works.

Deployment succeeding only means the build succeeded. This proves the
remote container can:
  - import clearance_agent from the shipped extra_packages
  - find parallel-web in the installed requirements
  - read PARALLEL_API_KEY from env_vars
  - reach api.parallel.ai from inside Google's network
  - call Gemini via Vertex (GOOGLE_GENAI_USE_VERTEXAI=TRUE), not AI Studio

Any of those can fail independently of a green deploy.

Usage:  python query_remote.py [RESOURCE_NAME]
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import vertexai  # noqa: E402
from vertexai import agent_engines  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "clearance-agent-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

PROMPT = (
    "Research clearance for one script entity: the Nike swoosh. "
    "Report the rights holder, registration number, and risk rating."
)


def resolve_resource() -> str:
    """Use the newest deployment unless one is named explicitly.

    agent_engines.create() always makes a NEW engine rather than replacing
    the old one, so hardcoding a resource name silently queries a stale
    deployment after any redeploy. That already happened once.
    """
    if len(sys.argv) > 1:
        return sys.argv[1]

    engines = list(agent_engines.list())
    if not engines:
        raise SystemExit("no deployments found — run deploy.py first")
    if len(engines) > 1:
        print(f"note: {len(engines)} deployments exist; using the most recent\n")
    # create_time is newest-last in practice; sort defensively.
    engines.sort(key=lambda e: getattr(e, "create_time", ""), reverse=True)
    return engines[0].resource_name


def main() -> None:
    vertexai.init(project=PROJECT, location=LOCATION)
    resource = resolve_resource()
    remote = agent_engines.get(resource)
    print(f"connected: {resource}\n")

    # Surface what the deployed object can actually do — the method set
    # differs from the local Agent and is not obvious from the docs.
    ops = [m for m in dir(remote) if not m.startswith("_") and callable(getattr(remote, m, None))]
    print(f"available methods: {ops}\n")
    print("querying...\n")

    if hasattr(remote, "stream_query"):
        for event in remote.stream_query(
            user_id="spike", message=PROMPT
        ):
            print(event)
    elif hasattr(remote, "query"):
        print(remote.query(input=PROMPT))
    else:
        print("!! neither stream_query nor query found — inspect methods above")


if __name__ == "__main__":
    main()
