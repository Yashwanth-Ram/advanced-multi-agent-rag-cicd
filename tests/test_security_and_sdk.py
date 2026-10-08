import importlib
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import ServiceUnavailable
from google.cloud import dlp_v2
from google.cloud import modelarmor_v1 as armor
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from pydantic import ValidationError

from deployment.deploy import CLASS_METHODS, runtime_config
from rag_agent.agents import create_agents
from rag_agent.governance import (
    Budget,
    Governance,
    LimitExceeded,
    PolicyBlocked,
    SecurityUnavailable,
    validate_armor_result,
)
from rag_agent.observability import MetadataExporter
from rag_agent.schemas import Answer, Route


@pytest.mark.parametrize(
    "state,invocation,error",
    [
        (2, 1, PolicyBlocked),
        (1, 2, SecurityUnavailable),
        (0, 1, SecurityUnavailable),
        (1, 3, SecurityUnavailable),
    ],
)
def test_armor_errors(state, invocation, error):
    with pytest.raises(error):
        validate_armor_result(
            armor.SanitizationResult(filter_match_state=state, invocation_result=invocation)
        )


def test_armor_success():
    validate_armor_result(armor.SanitizationResult(filter_match_state=1, invocation_result=1))


def test_skipped_filter():
    r = armor.SanitizationResult(
        filter_match_state=1,
        invocation_result=1,
        filter_results={
            "pi": {"pi_and_jailbreak_filter_result": {"execution_state": 2, "match_state": 1}}
        },
    )
    with pytest.raises(SecurityUnavailable):
        validate_armor_result(r)


def test_budget():
    b = Budget(1, 1)
    b.model()
    b.retrieval()
    with pytest.raises(LimitExceeded):
        b.model()
    with pytest.raises(LimitExceeded):
        b.retrieval()


def test_schema_route_safety():
    with pytest.raises(ValidationError):
        Route(tasks=[{"department": "finance", "question": "budget"}])
    with pytest.raises(ValidationError):
        Route(tasks=[])
    with pytest.raises(ValidationError):
        Answer(status="answered", claims=[])


def test_no_tools_and_compatible_sdk(settings):
    agents = create_agents(settings)
    assert len(agents) == 6 and all(not a.tools for a in agents.values())
    from google.adk.agents.run_config import RunConfig

    assert RunConfig(max_llm_calls=1).max_llm_calls == 1
    from agentplatform import Client
    from google.auth.credentials import AnonymousCredentials

    c = Client(
        project=settings.project, location=settings.location, credentials=AnonymousCredentials()
    )
    assert (
        callable(c.runtimes.create)
        and callable(c.runtimes.update)
        and callable(c.rag.retrieve_contexts)
    )
    assert runtime_config().entrypoint_module == "rag_agent.application"
    assert CLASS_METHODS[0]["name"] == "async_query"


class Collect:
    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def test_trace_export_removes_content_and_encodes():
    sink = Collect()
    p = TracerProvider(sampler=ALWAYS_ON)
    p.add_span_processor(SimpleSpanProcessor(MetadataExporter(sink)))
    with p.get_tracer("test").start_as_current_span("generate_content secret@example.com") as s:
        s.set_attribute("input.value", "secret@example.com")
        s.set_attribute("app.request_id", "abc")
        s.add_event("exception", {"exception.message": "private prompt"})
    exported = sink.spans[0]
    assert (
        exported.name == "adk.operation"
        and exported.attributes == {"app.request_id": "abc"}
        and not exported.events
    )
    serialized = encode_spans(sink.spans).SerializeToString()
    assert b"secret@example.com" not in serialized and b"private prompt" not in serialized
    p.shutdown()


async def test_google_security_sdk_request_shapes(settings, monkeypatch):
    class ArmorClient:
        def sanitize_user_prompt(self, request, **kwargs):
            armor.SanitizeUserPromptRequest(request)
            return armor.SanitizeUserPromptResponse(
                sanitization_result={"filter_match_state": 1, "invocation_result": 1}
            )

        def sanitize_model_response(self, request, **kwargs):
            armor.SanitizeModelResponseRequest(request)
            return armor.SanitizeModelResponseResponse(
                sanitization_result={"filter_match_state": 1, "invocation_result": 1}
            )

    class DlpClient:
        def deidentify_content(self, request, **kwargs):
            dlp_v2.DeidentifyContentRequest(request)
            return dlp_v2.DeidentifyContentResponse(item={"value": "safe"})

    monkeypatch.setattr(armor, "ModelArmorClient", lambda **kwargs: ArmorClient())
    monkeypatch.setattr(dlp_v2, "DlpServiceClient", lambda: DlpClient())
    g = Governance(settings)
    assert await g.protect("hello") == "safe"
    assert await g.protect("hello", output=True) == "safe"


async def test_api_failure_maps_to_closed_policy(settings, monkeypatch):
    g = object.__new__(Governance)
    g.s = settings

    def fail(**kwargs):
        raise ServiceUnavailable("private internal error")

    g.armor = SimpleNamespace(sanitize_user_prompt=fail)
    with pytest.raises(SecurityUnavailable):
        await g.screen("policy")


def test_template_creation_request_shapes(settings, monkeypatch):
    from google.api_core.exceptions import NotFound

    from scripts import create_security_templates as script

    def missing(**kwargs):
        raise NotFound("no template")

    class ArmorClient:
        get_template = staticmethod(missing)

        def create_template(self, request):
            armor.CreateTemplateRequest(request)

    class DlpClient:
        get_inspect_template = staticmethod(missing)
        get_deidentify_template = staticmethod(missing)

        def create_inspect_template(self, request):
            dlp_v2.CreateInspectTemplateRequest(request)

        def create_deidentify_template(self, request):
            dlp_v2.CreateDeidentifyTemplateRequest(request)

    monkeypatch.setattr(script.armor, "ModelArmorClient", lambda **kwargs: ArmorClient())
    monkeypatch.setattr(script.dlp_v2, "DlpServiceClient", lambda: DlpClient())
    monkeypatch.setattr("sys.argv", ["create_security_templates"])
    script.main()


@pytest.mark.parametrize(
    "module",
    [
        "rag_agent.application",
        "scripts.query",
        "deployment.deploy",
        "scripts.trace_smoke",
        "scripts.preflight",
        "evaluation.run",
    ],
)
def test_imports_without_cloud_credentials(module):
    importlib.import_module(module)


async def test_real_adk_runner_with_local_model(settings):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from rag_agent.agents import AgentExecutor
    from rag_agent.schemas import Queries

    class FakeModel(BaseLlm):
        model: str = "offline-fixture"

        async def generate_content_async(self, llm_request, stream=False):
            assert llm_request.contents and llm_request.config.response_schema
            yield LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text='{"queries":["onboarding"]}')]
                )
            )

    class Security:
        async def screen(self, *args, **kwargs):
            pass

        async def redact(self, text):
            return text

    executor = AgentExecutor(settings, Security())
    executor.agents["expander"].model = FakeModel()
    result = await executor.run("expander", {"question": "onboarding"}, Queries, Budget(1, 0))
    assert result.queries == ["onboarding"]


def test_google_trace_translation_removes_content():
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

    class Client:
        request = None

        def batch_write_spans(self, request):
            self.request = request

    client = Client()
    exporter = CloudTraceSpanExporter(project_id="test", client=client)
    p = TracerProvider(sampler=ALWAYS_ON)
    p.add_span_processor(SimpleSpanProcessor(MetadataExporter(exporter)))
    with p.get_tracer("test").start_as_current_span("rag.smoke") as s:
        s.set_attribute("input.value", "secret@example.com")
        s.set_attribute("app.request_id", "shared-id")
    assert client.request is not None
    serialized = type(client.request).serialize(client.request)
    assert b"secret@example.com" not in serialized and b"shared-id" in serialized
    p.shutdown()


def test_native_adk_evalset_schema():
    from pathlib import Path

    from google.adk.evaluation.eval_set import EvalSet

    value = EvalSet.model_validate_json(Path("evaluation/router.evalset.json").read_text())
    assert len(value.eval_cases) == 3
