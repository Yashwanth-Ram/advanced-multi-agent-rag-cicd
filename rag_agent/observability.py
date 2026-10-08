"""One isolated, privacy-filtered provider exports identical spans to both backends."""

import json
import logging
import sys
import threading
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

# Only explicitly approved metadata leaves the process, even if instrumentation
# adds prompts, responses, source URIs, exception messages or tool arguments.
SAFE = {
    "app.request_id",
    "app.release",
    "app.stage",
    "app.status",
    "app.department",
    "app.model_calls",
    "app.retrieval_calls",
    "app.citation_count",
    "app.candidate_count",
    "app.result_count",
    "openinference.span.kind",
    "llm.model_name",
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "llm.token_count.total",
}


class SafeSpan:
    def __init__(self, span):
        self.original = span

    def __getattr__(self, key):
        return getattr(self.original, key)

    @property
    def attributes(self):
        return {k: v for k, v in (self.original.attributes or {}).items() if k in SAFE}

    @property
    def events(self):
        return ()

    @property
    def status(self):
        return trace.Status(self.original.status.status_code)

    @property
    def name(self):
        name = self.original.name
        return name if name.startswith("rag.") else "adk.operation"


class MetadataExporter(SpanExporter):
    def __init__(self, delegate):
        self.delegate = delegate

    def export(self, spans):
        return self.delegate.export([SafeSpan(x) for x in spans])

    def shutdown(self):
        self.delegate.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.delegate.force_flush(timeout_millis)


_lock = threading.Lock()
_provider = None


def initialize(settings):
    global _provider
    with _lock:
        if _provider is not None:
            return _provider
        provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=Resource.create(
                {
                    "service.name": "advanced-rag-dev",
                    "service.version": settings.release,
                    "openinference.project.name": settings.phoenix_project,
                }
            ),
        )
        if settings.cloud_trace:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            provider.add_span_processor(
                SimpleSpanProcessor(
                    MetadataExporter(CloudTraceSpanExporter(project_id=settings.project))
                )
            )
        if settings.phoenix:
            from google.cloud import secretmanager
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            sm = secretmanager.SecretManagerServiceClient()
            key = (
                sm.access_secret_version(request={"name": settings.phoenix_secret})
                .payload.data.decode()
                .strip()
            )
            exporter = OTLPSpanExporter(
                endpoint=settings.phoenix_endpoint,
                headers={
                    "Authorization": f"Bearer {key}",
                    "x-phoenix-project-name": settings.phoenix_project,
                },
                timeout=10,
            )
            provider.add_span_processor(SimpleSpanProcessor(MetadataExporter(exporter)))
        if settings.phoenix or settings.cloud_trace:
            from openinference.instrumentation import TraceConfig
            from openinference.instrumentation.google_adk import GoogleADKInstrumentor

            GoogleADKInstrumentor().instrument(
                tracer_provider=provider,
                config=TraceConfig(
                    hide_inputs=True,
                    hide_outputs=True,
                    hide_input_messages=True,
                    hide_output_messages=True,
                    hide_llm_invocation_parameters=True,
                    hide_llm_tools=True,
                    hide_prompts=True,
                ),
            )
        # Never replace the runtime's global provider. The instrumentor routes ADK
        # spans through this provider and both exporters; GCP spans remain available.
        _provider = provider
        return provider


@contextmanager
def span(provider, name: str, **attributes):
    with provider.get_tracer("advanced_rag").start_as_current_span(
        "rag." + name, attributes=attributes, record_exception=False
    ) as current:
        yield current


_audit_logger = logging.getLogger("advanced_rag.audit")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
if not _audit_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(handler)


def audit(**metadata):
    _audit_logger.info(json.dumps({"severity": "INFO", **metadata}))
