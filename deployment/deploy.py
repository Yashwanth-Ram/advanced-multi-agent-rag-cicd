"""Create/update ONE dev runtime with source deployment; no live smoke invocation."""

import argparse
import json
import os
from pathlib import Path

import agentplatform
from agentplatform import types
from dotenv import dotenv_values, load_dotenv

from rag_agent.settings import get_settings

# Only explicit nonsecret config is forwarded. Secrets are fetched inside runtime.
ENV_KEYS = {
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "AGENT_MODEL",
    "ADVANCED_RAG_MODEL",
    "RANKER_MODEL",
    "HR_CORPUS",
    "TECHNICAL_CORPUS",
    "MANAGEMENT_CORPUS",
    "COMMON_CORPUS",
    "MODEL_ARMOR_TEMPLATE",
    "DLP_INSPECT_TEMPLATE",
    "DLP_DEIDENTIFY_TEMPLATE",
    "DEV_ALLOWED_DEPARTMENTS",
    "RAG_QUERY_COUNT",
    "RAG_PER_QUERY_TOP_K",
    "RAG_FINAL_TOP_K",
    "MAX_MODEL_CALLS",
    "MAX_RETRIEVAL_CALLS",
    "MAX_INPUT_CHARS",
    "MAX_PASSAGE_CHARS",
    "MAX_OUTPUT_TOKENS",
    "REQUEST_TIMEOUT_SECONDS",
    "CLOUD_TRACE_ENABLED",
    "PHOENIX_ENABLED",
    "PHOENIX_PROJECT_NAME",
    "PHOENIX_COLLECTOR_ENDPOINT",
    "PHOENIX_API_KEY_SECRET",
    "RELEASE_VERSION",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
}
CLASS_METHODS = [
    {
        "name": "async_query",
        "api_mode": "async",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "history": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"role": {"type": "string"}, "text": {"type": "string"}},
                    },
                },
            },
            "required": ["question"],
        },
    }
]


def runtime_config():
    load_dotenv()
    values = {**dotenv_values(".env.example"), **os.environ}
    env = {k: str(values[k]) for k in ENV_KEYS if values.get(k) is not None}
    env["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    return types.AgentRuntimeConfig(
        source_packages=["rag_agent", "requirements.txt"],
        entrypoint_module="rag_agent.application",
        entrypoint_object="app",
        requirements_file="requirements.txt",
        class_methods=CLASS_METHODS,
        display_name="advanced-rag-dev",
        description="Governed HR/management/technical RAG",
        service_account=values["RUNTIME_SERVICE_ACCOUNT"],
        env_vars=env,
        python_version="3.12",
        min_instances=0,
        max_instances=2,
        labels={"environment": "dev", "release": env.get("RELEASE_VERSION", "dev")[:63]},
        agent_framework="custom",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    s = get_settings()
    config = runtime_config()
    if args.dry_run:
        print(config.model_dump_json(indent=2, exclude_none=True))
        return
    name = os.environ.get("AGENT_RUNTIME_RESOURCE", "").strip()
    client = agentplatform.Client(project=s.project, location=s.location)
    remote = (
        client.runtimes.update(name=name, config=config)
        if name
        else client.runtimes.create(config=config)
    )
    resource = remote.api_resource.name
    Path("artifacts").mkdir(exist_ok=True)
    manifest = {
        "resource_name": resource,
        "release": s.release,
        "environment": "dev",
        "commit": os.environ.get("RELEASE_COMMIT", ""),
        "model": s.model,
        "ranker": s.ranker,
        "live_checks": "not_run",
    }
    Path("artifacts/deployment.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
