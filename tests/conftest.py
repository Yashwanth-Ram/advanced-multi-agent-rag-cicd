from pathlib import Path

import pytest
from dotenv import dotenv_values
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from rag_agent.settings import get_settings


@pytest.fixture
def settings(monkeypatch):
    for k, v in dotenv_values(Path(".env.example")).items():
        if v is not None:
            monkeypatch.setenv(k, v)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    result = get_settings()
    yield result
    get_settings.cache_clear()


@pytest.fixture
def provider():
    p = TracerProvider(sampler=ALWAYS_ON)
    yield p
    p.shutdown()
