"""Deploy the clearance agent to Vertex AI Agent Engine.

Verified against google-cloud-aiplatform 1.163.0 by introspection:
    vertexai.agent_engines.create(
        agent_engine: BaseAgent | Queryable | ...,
        *, requirements, display_name, description, gcs_dir_name,
        extra_packages, env_vars, build_options, service_account,
        min_instances, max_instances, resource_limits,
        container_concurrency, encryption_spec,
    ) -> AgentEngine
    agent_engines.list(filter=...) / get(resource_name) / delete(resource_name, force=)

Usage:
    python deploy.py            # deploy
    python deploy.py --list     # what is already deployed
    python deploy.py --delete RESOURCE_NAME

COST: a deployed Agent Engine bills for provisioned time, not just requests.
Tear it down when you are not demoing. See --delete.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import vertexai  # noqa: E402
from google.cloud.aiplatform_v1.types.env_var import SecretRef  # noqa: E402
from vertexai import agent_engines  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "clearance-agent-2026")
# us-central1 has the broadest Agent Engine availability. europe-north1 may
# not host it — change only after confirming the region is supported.
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.environ.get("STAGING_BUCKET", f"gs://{PROJECT}-agent-staging")

# The deployed agent talks to Gemini via VERTEX, whose model catalogue is
# NOT the same as AI Studio's. Verified 2026-08-11 against this project in
# us-central1: every 3.x model 404s ("not found or your project does not
# have access to it") even though models.list() returns them. Only
# gemini-2.5-flash actually responds — the very model AI Studio refuses to
# serve to keys created now. So local and deployed run different models by
# necessity. Re-check with scratch/test_vertex_models.py on a new project.
VERTEX_MODEL = os.environ.get("VERTEX_CLEARANCE_MODEL", "gemini-2.5-flash")
REQUIREMENTS = [
    # Pinned to what is verified working locally. Do not loosen — ADK 2.x
    # has breaking changes from 1.x and a floating pin could deploy 1.x.
    "google-adk>=2.6.0,<3.0.0",
    "parallel-web>=1.0.1",
    "python-dotenv>=1.0.0",
    "google-cloud-aiplatform[agent_engines,adk]>=1.101.0",
    # The agent is cloudpickled locally and unpickled in the deployed
    # container. Mismatched versions across that boundary fail obscurely,
    # so pin to whatever is installed locally (3.1.2 as of 2026-08-11).
    "cloudpickle==3.1.2",
]


def ensure_bucket() -> None:
    """Agent Engine needs a staging bucket to upload the packaged agent."""
    from google.cloud import storage

    client = storage.Client(project=PROJECT)
    name = BUCKET.replace("gs://", "")
    if client.lookup_bucket(name) is None:
        print(f"creating staging bucket {name} in {LOCATION}...")
        client.create_bucket(name, location=LOCATION)
    else:
        print(f"staging bucket {name} exists")


def deploy() -> None:
    ensure_bucket()
    vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=BUCKET)

    from clearance_agent.agent import root_agent

    # The agent is cloudpickled with whatever model config.py resolved
    # LOCALLY (an AI Studio model). Override before pickling so the
    # deployed copy carries a model Vertex will actually serve.
    root_agent.model = VERTEX_MODEL

    print(f"deploying {root_agent.name} (model={root_agent.model}) to {LOCATION}...")
    print("this takes several minutes — it builds a container.")

    remote = agent_engines.create(
        root_agent,
        requirements=REQUIREMENTS,
        # Ships our package source so the deployed agent can import it.
        extra_packages=["./clearance_agent"],
        display_name="Script Clearance Agent",
        description=(
            "Researches rights-clearance risk for real-world entities "
            "appearing in a screenplay, with a full citation trail."
        ),
        env_vars={
            # Verified against google-cloud-aiplatform by introspection:
            # SecretRef(secret, version) — `secret` is the SHORT secret
            # name (Format: {secret_name}), not a full resource path.
            # Secret created in Secret Manager (project clearance-agent-2026)
            # via `gcloud secrets create PARALLEL_API_KEY`; the Reasoning
            # Engine service agent
            # (service-<project-number>@gcp-sa-aiplatform-re.iam.gserviceaccount.com)
            # was granted roles/secretmanager.secretAccessor on this secret
            # specifically — a deploy succeeding does not imply this
            # binding exists, it fails at runtime instead, so this was
            # checked and granted before relying on it here.
            "PARALLEL_API_KEY": SecretRef(secret="PARALLEL_API_KEY", version="latest"),
            "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
            "CLEARANCE_MODEL": VERTEX_MODEL,
        },
        # Keep the floor at zero so an idle deployment is not billing.
        min_instances=0,
        max_instances=1,
    )

    print("\nDEPLOYED")
    print(f"  resource_name: {remote.resource_name}")
    print("\nTear down when done:")
    print(f'  python deploy.py --delete "{remote.resource_name}"')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list deployed agents")
    ap.add_argument("--delete", metavar="RESOURCE_NAME", help="delete a deployment")
    args = ap.parse_args()

    vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=BUCKET)

    if args.list:
        found = list(agent_engines.list())
        if not found:
            print("no deployments")
        for a in found:
            print(f"  {a.resource_name}")
        return

    if args.delete:
        print(f"deleting {args.delete}...")
        agent_engines.delete(args.delete, force=True)
        print("deleted")
        return

    deploy()


if __name__ == "__main__":
    main()
