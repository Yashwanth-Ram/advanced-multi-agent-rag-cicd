"""Explicit live, read-only resource checks; no reimport or deployment."""

import argparse
import asyncio
import json
import subprocess

import agentplatform
from google import genai
from google.api_core.exceptions import GoogleAPICallError
from google.genai.errors import APIError

from rag_agent.governance import Governance, PolicyBlocked, SecurityUnavailable
from rag_agent.settings import get_settings


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", action="store_true", help="Also make two small paid Gemini calls"
    )
    args = parser.parse_args()
    s = get_settings()
    checks = []
    number = subprocess.check_output(
        ["gcloud", "projects", "describe", s.project, "--format=value(projectNumber)"], text=True
    ).strip()
    checks.append(
        {
            "check": "project_number",
            "status": "PASS",
            "value": number,
            "cross_project_corpora": sorted(
                {c.split("/")[1] for c in s.corpora.values()} - {s.project, number}
            ),
        }
    )
    for d, corpus in s.corpora.items():
        try:
            client = agentplatform.Client(
                project=corpus.split("/")[1], location=corpus.split("/")[3]
            )
            value = await asyncio.to_thread(client.rag.get_corpus, name=corpus)
            checks.append(
                {
                    "check": d + "_corpus",
                    "status": "PASS",
                    "metadata": value.model_dump(mode="json"),
                }
            )
        except (GoogleAPICallError, APIError) as exc:
            checks.append(
                {"check": d + "_corpus", "status": "FAIL", "error_type": type(exc).__name__}
            )
    g = Governance(s)
    try:
        await g.protect("What is the onboarding policy?")
        redacted = await g.redact("Contact example.person@example.com")
        checks.append(
            {
                "check": "security_templates",
                "status": "PASS" if "example.person@example.com" not in redacted else "FAIL",
            }
        )
    except (GoogleAPICallError, PolicyBlocked, SecurityUnavailable) as exc:
        checks.append(
            {"check": "security_templates", "status": "FAIL", "error_type": type(exc).__name__}
        )
    if args.models:
        with genai.Client(vertexai=True, project=s.project, location=s.location) as c:
            for model in set((s.model, s.expansion_model)):
                await c.aio.models.generate_content(
                    model=model, contents="Reply OK", config={"max_output_tokens": 64}
                )
                checks.append({"check": model, "status": "PASS"})
    print(json.dumps(checks, indent=2, default=str))
    if any(x["status"] == "FAIL" for x in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
