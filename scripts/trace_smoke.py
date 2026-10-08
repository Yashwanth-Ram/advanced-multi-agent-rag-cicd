"""Send one metadata-only span to configured collectors (a live check)."""

from uuid import uuid4

from rag_agent.observability import initialize, span
from rag_agent.settings import get_settings


def main():
    s = get_settings()
    if not (s.cloud_trace or s.phoenix):
        raise SystemExit("Enable Cloud Trace and/or Phoenix first")
    provider = initialize(s)
    rid = uuid4().hex
    with span(
        provider,
        "smoke",
        **{"app.request_id": rid, "app.release": s.release, "app.status": "smoke"},
    ):
        pass
    provider.force_flush(timeout_millis=10000)
    print("Export attempted. Find app.request_id in each destination:", rid)
    print("Confirm visibility in the consoles; exporter errors appear on stderr.")


if __name__ == "__main__":
    main()
