"""Validated nonsecret configuration; .env never overrides real environment."""

import os
import re
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

DEPARTMENTS = ("hr", "management", "technical")


class Settings(BaseModel):
    project: str
    location: str = "us-central1"
    model: str = "gemini-2.5-flash"
    expansion_model: str = "gemini-2.5-flash"
    ranker: str = "semantic-ranker-default@latest"
    corpora: dict[str, str]
    armor_template: str
    inspect_template: str
    deidentify_template: str
    allowed_departments: list[str] = Field(default_factory=lambda: list(DEPARTMENTS))
    query_count: int = Field(default=3, ge=1, le=3)
    per_query_k: int = Field(default=10, ge=1, le=10)
    final_k: int = Field(default=6, ge=1, le=6)
    max_model_calls: int = Field(default=10, ge=1, le=20)
    max_retrieval_calls: int = Field(default=18, ge=1, le=18)
    max_input_chars: int = Field(default=8000, ge=100, le=12000)
    max_passage_chars: int = Field(default=4000, ge=100, le=6000)
    max_output_tokens: int = Field(default=2048, ge=128, le=4096)
    timeout: int = Field(default=240, ge=10, le=600)
    cloud_trace: bool = False
    phoenix: bool = False
    phoenix_project: str = "advanced-rag-dev"
    phoenix_endpoint: str = ""
    phoenix_secret: str = ""
    release: str = "local-dev"

    @field_validator("corpora")
    @classmethod
    def validate_corpora(cls, value):
        if set(value) != set(DEPARTMENTS) | {"common"}:
            raise ValueError("Exactly HR, management, technical and common corpora are required")
        for resource in value.values():
            if not re.fullmatch(
                r"projects/[a-z0-9-]+/locations/[a-z0-9-]+/ragCorpora/[0-9]+", resource
            ):
                raise ValueError("Invalid corpus resource name")
        if len(set(value.values())) != 4:
            raise ValueError("The four corpus resource names must be distinct")
        return value

    @model_validator(mode="after")
    def validate_policy(self):
        if not self.allowed_departments or not set(self.allowed_departments) <= set(DEPARTMENTS):
            raise ValueError("DEV_ALLOWED_DEPARTMENTS must contain approved departments")
        for name, resource in (
            ("armor", self.armor_template),
            ("inspect", self.inspect_template),
            ("deidentify", self.deidentify_template),
        ):
            kind = {
                "armor": "templates",
                "inspect": "inspectTemplates",
                "deidentify": "deidentifyTemplates",
            }[name]
            if not re.fullmatch(
                rf"projects/[a-z0-9-]+/locations/[a-z0-9-]+/{kind}/[a-zA-Z0-9_-]+", resource
            ):
                raise ValueError(f"Configure the {name} template resource")
        if self.phoenix and (
            not self.phoenix_endpoint.startswith("https://")
            or not self.phoenix_endpoint.endswith("/v1/traces")
            or not self.phoenix_secret
        ):
            raise ValueError(
                "Phoenix needs a full HTTPS /v1/traces endpoint and Secret Manager version"
            )
        return self

    def permitted_corpora(self, department: str) -> list[str]:
        if department not in self.allowed_departments:
            raise PermissionError("Department access denied")
        return [self.corpora[department], self.corpora["common"]]


@lru_cache
def get_settings() -> Settings:
    load_dotenv(override=False)
    env = os.environ

    def num(key, default):
        return int(env.get(key, default))

    def flag(key):
        return env.get(key, "false").lower() in {"true", "1", "yes"}

    return Settings(
        project=env["GOOGLE_CLOUD_PROJECT"],
        location=env.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        model=env.get("AGENT_MODEL", "gemini-2.5-flash"),
        expansion_model=env.get("ADVANCED_RAG_MODEL", "gemini-2.5-flash"),
        ranker=env.get("RANKER_MODEL", "semantic-ranker-default@latest"),
        corpora={d: env[d.upper() + "_CORPUS"] for d in (*DEPARTMENTS, "common")},
        armor_template=env["MODEL_ARMOR_TEMPLATE"],
        inspect_template=env["DLP_INSPECT_TEMPLATE"],
        deidentify_template=env["DLP_DEIDENTIFY_TEMPLATE"],
        allowed_departments=[
            x.strip() for x in env.get("DEV_ALLOWED_DEPARTMENTS", ",".join(DEPARTMENTS)).split(",")
        ],
        query_count=num("RAG_QUERY_COUNT", 3),
        per_query_k=num("RAG_PER_QUERY_TOP_K", 10),
        final_k=num("RAG_FINAL_TOP_K", 6),
        max_model_calls=num("MAX_MODEL_CALLS", 10),
        max_retrieval_calls=num("MAX_RETRIEVAL_CALLS", 18),
        max_input_chars=num("MAX_INPUT_CHARS", 8000),
        max_passage_chars=num("MAX_PASSAGE_CHARS", 4000),
        max_output_tokens=num("MAX_OUTPUT_TOKENS", 2048),
        timeout=num("REQUEST_TIMEOUT_SECONDS", 240),
        cloud_trace=flag("CLOUD_TRACE_ENABLED"),
        phoenix=flag("PHOENIX_ENABLED"),
        phoenix_project=env.get("PHOENIX_PROJECT_NAME", "advanced-rag-dev"),
        phoenix_endpoint=env.get("PHOENIX_COLLECTOR_ENDPOINT", ""),
        phoenix_secret=env.get("PHOENIX_API_KEY_SECRET", ""),
        release=env.get("RELEASE_VERSION", "local-dev"),
    )
