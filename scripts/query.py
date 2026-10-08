"""Run a guarded local request, or invoke the same method on Agent Runtime."""

import argparse
import asyncio
import json
from pathlib import Path

from rag_agent.application import CompanyRagApplication
from rag_agent.settings import get_settings


async def run(args):
    history = json.loads(Path(args.history).read_text()) if args.history else []
    if args.remote:
        import os

        from dotenv import load_dotenv

        load_dotenv()
        s = get_settings()
        resource = os.environ.get("AGENT_RUNTIME_RESOURCE", "")
        if not resource:
            raise SystemExit("Set AGENT_RUNTIME_RESOURCE from artifacts/deployment.json")
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = AuthorizedSession(credentials)
        url = f"https://{s.location}-aiplatform.googleapis.com/v1/{resource}:query"
        response = await asyncio.to_thread(
            session.post,
            url,
            json={
                "classMethod": "async_query",
                "input": {"question": args.question, "history": history},
            },
            timeout=300,
        )
        response.raise_for_status()
        result = response.json().get("output", response.json())
    else:
        result = await CompanyRagApplication().async_query(args.question, history)
    print(json.dumps(result, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--history", help="JSON file with at most eight {role,text} turns")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
