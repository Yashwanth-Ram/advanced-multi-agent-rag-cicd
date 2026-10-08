"""Explicit fail-closed screening and DLP. No raw request text in logs."""

import asyncio
from dataclasses import dataclass

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import dlp_v2
from google.cloud import modelarmor_v1 as armor


class PolicyBlocked(RuntimeError):
    pass


class SecurityUnavailable(RuntimeError):
    pass


class LimitExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    max_model: int
    max_retrieval: int
    model_calls: int = 0
    retrieval_calls: int = 0

    def model(self):
        if self.model_calls >= self.max_model:
            raise LimitExceeded("Model-call limit")
        self.model_calls += 1

    def retrieval(self):
        if self.retrieval_calls >= self.max_retrieval:
            raise LimitExceeded("Retrieval-call limit")
        self.retrieval_calls += 1


def validate_armor_result(result):
    if result.invocation_result != armor.InvocationResult.SUCCESS:
        raise SecurityUnavailable("Model Armor did not complete screening")
    if result.filter_match_state == armor.FilterMatchState.MATCH_FOUND:
        raise PolicyBlocked("Content rejected by Model Armor")
    if result.filter_match_state != armor.FilterMatchState.NO_MATCH_FOUND:
        raise SecurityUnavailable("Model Armor returned an indeterminate result")
    # A successful invocation alone is insufficient if a configured filter was skipped.
    for value in result.filter_results.values():
        body = armor.FilterResult.to_dict(value, use_integers_for_enums=False)
        for item in body.values():
            if isinstance(item, dict) and item.get("execution_state") not in (
                None,
                "EXECUTION_SUCCESS",
            ):
                raise SecurityUnavailable("A Model Armor filter did not complete")


class Governance:
    def __init__(self, settings):
        self.s = settings
        region = settings.armor_template.split("/")[3]
        self.armor = armor.ModelArmorClient(
            client_options=ClientOptions(api_endpoint=f"modelarmor.{region}.rep.googleapis.com")
        )
        self.dlp = dlp_v2.DlpServiceClient()
        self.dlp_parent = settings.inspect_template.rsplit("/inspectTemplates/", 1)[0]

    async def screen(self, text: str, output: bool = False) -> None:
        method = self.armor.sanitize_model_response if output else self.armor.sanitize_user_prompt
        key = "model_response_data" if output else "user_prompt_data"
        try:
            response = await asyncio.to_thread(
                method,
                request={"name": self.s.armor_template, key: {"text": text}},
                timeout=30,
                retry=None,
            )
            validate_armor_result(response.sanitization_result)
        except GoogleAPICallError as exc:
            raise SecurityUnavailable("Model Armor service unavailable") from exc

    async def redact(self, text: str) -> str:
        try:
            response = await asyncio.to_thread(
                self.dlp.deidentify_content,
                request={
                    "parent": self.dlp_parent,
                    "inspect_template_name": self.s.inspect_template,
                    "deidentify_template_name": self.s.deidentify_template,
                    "item": {"value": text},
                },
                timeout=30,
                retry=None,
            )
            return response.item.value
        except GoogleAPICallError as exc:
            raise SecurityUnavailable("Sensitive Data Protection unavailable") from exc

    async def protect(self, text: str, output: bool = False) -> str:
        # Detect injections on original text; redact before any model/context release.
        await self.screen(text, output=output)
        return await self.redact(text)
