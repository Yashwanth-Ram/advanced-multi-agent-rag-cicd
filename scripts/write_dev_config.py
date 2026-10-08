"""Export only the reviewed nonsecret dev configuration for Cloud Build."""

import os
import shlex
from pathlib import Path

from dotenv import load_dotenv

from deployment.deploy import ENV_KEYS
from rag_agent.settings import get_settings


def main():
    load_dotenv()
    get_settings()
    keys = ENV_KEYS | {"RUNTIME_SERVICE_ACCOUNT", "ARTIFACT_BUCKET"}
    values = {k: os.environ[k] for k in sorted(keys) if k in os.environ}
    Path("config/dev.env").write_text(
        "# Generated nonsecret dev configuration\n"
        + "\n".join(k + "=" + shlex.quote(v) for k, v in values.items())
        + "\n"
    )
    print("Wrote config/dev.env. Review before committing; API keys are excluded.")


if __name__ == "__main__":
    main()
