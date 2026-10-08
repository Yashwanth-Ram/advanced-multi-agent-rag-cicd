"""Runtime entrypoint. IAM authenticates trusted dev callers; no role arguments."""

import asyncio
import re
import time
import uuid

from rag_agent.agents import AgentExecutor
from rag_agent.governance import (
    Budget,
    Governance,
    LimitExceeded,
    PolicyBlocked,
    SecurityUnavailable,
)
from rag_agent.observability import audit, initialize, span
from rag_agent.retrieval import CloudRetriever, RetrievalPipeline
from rag_agent.schemas import Answer, Route
from rag_agent.settings import get_settings


class CitationError(ValueError):
    pass


def validate_citations(answer, passages):
    known = {p.id for p in passages}
    # Only the renderer may add citations. Reject IDs hidden in generated prose.
    if any(re.search(r"\[S[A-Za-z0-9_-]+\]", c.text) for c in answer.claims):
        raise CitationError("Citation markup must be supplied as source_ids, not prose")
    if any(s not in known for c in answer.claims for s in c.source_ids):
        raise CitationError("Answer referenced evidence that was not retrieved")


def render(answer):
    if answer.status == "insufficient_evidence":
        return "I do not have enough evidence in the approved documents to answer this question."
    prefix = (
        "The approved documents contain conflicting information.\n"
        if answer.status == "conflicting_evidence"
        else ""
    )
    return prefix + "\n".join(
        c.text + " " + " ".join(f"[{s}]" for s in c.source_ids) for c in answer.claims
    )


class CompanyRagApplication:
    def __init__(self, settings=None, governance=None, executor=None, cloud=None, provider=None):
        self._s = settings
        self._gov = governance
        self._executor = executor
        self._cloud = cloud
        self._provider = provider

    def setup(self):
        self._s = self._s or get_settings()
        self._provider = self._provider or initialize(self._s)
        self._gov = self._gov or Governance(self._s)
        self._executor = self._executor or AgentExecutor(self._s, self._gov)
        self._cloud = self._cloud or CloudRetriever(self._s)

    async def async_query(self, question: str, history: list[dict] | None = None) -> dict:
        request_id = uuid.uuid4().hex
        start = time.monotonic()
        self.setup()
        budget = Budget(self._s.max_model_calls, self._s.max_retrieval_calls)
        audit_status = "error"
        try:
            async with asyncio.timeout(self._s.timeout):
                with span(
                    self._provider,
                    "request",
                    **{"app.request_id": request_id, "app.release": self._s.release},
                ) as current:
                    result = await self._query(question, history or [], budget)
                    result.update(
                        request_id=request_id,
                        release=self._s.release,
                        model_calls=budget.model_calls,
                        retrieval_calls=budget.retrieval_calls,
                    )
                    audit_status = result["status"]
                    current.set_attribute("app.status", result["status"])
                    current.set_attribute("app.model_calls", budget.model_calls)
                    current.set_attribute("app.retrieval_calls", budget.retrieval_calls)
                    current.set_attribute("app.citation_count", len(result.get("sources", [])))
                    return result
        except (PolicyBlocked, PermissionError):
            audit_status = "blocked"
            return {
                "status": "blocked",
                "answer": "This request was blocked by the configured policy.",
                "sources": [],
                "request_id": request_id,
            }
        except (SecurityUnavailable, LimitExceeded, TimeoutError):
            audit_status = "unavailable"
            return {
                "status": "unavailable",
                "answer": "The request could not complete safely. Retry later or check the request logs.",
                "sources": [],
                "request_id": request_id,
            }
        finally:
            audit(
                request_id=request_id,
                release=self._s.release,
                event="request_completed",
                status=audit_status,
                duration_ms=round((time.monotonic() - start) * 1000),
                model_calls=budget.model_calls,
                retrieval_calls=budget.retrieval_calls,
            )
            # Synchronous processors already export spans; flush both destinations.
            self._provider.force_flush(timeout_millis=10000)

    async def _query(self, question, history, budget):
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > self._s.max_input_chars
        ):
            raise PolicyBlocked("Invalid question size")
        if not isinstance(history, list) or len(history) > 8:
            raise PolicyBlocked("Invalid history")
        clean_history = []
        history_chars = 0
        for turn in history:
            if (
                not isinstance(turn, dict)
                or set(turn) != {"role", "text"}
                or turn["role"] not in {"user", "assistant"}
            ):
                raise PolicyBlocked("Invalid history")
            if not isinstance(turn["text"], str):
                raise PolicyBlocked("Invalid history text")
            history_chars += len(turn["text"])
            if history_chars > self._s.max_input_chars:
                raise PolicyBlocked("History too large")
            clean_history.append(
                {"role": turn["role"], "text": await self._gov.protect(turn["text"])}
            )
        with span(self._provider, "input_screening"):
            question = await self._gov.protect(question)
        with span(self._provider, "routing"):
            route = await self._executor.run(
                "router", {"question": question, "recent_history": clean_history}, Route, budget
            )
        if route.clarification:
            text = await self._gov.protect(route.clarification, output=True)
            return {"status": "clarification", "answer": text, "sources": [], "departments": []}
        # Check the whole route before reading ANY corpus, preventing partial leakage.
        for task in route.tasks:
            self._s.permitted_corpora(task.department)
        pipeline = RetrievalPipeline(
            self._s, self._cloud, self._executor, self._gov, self._provider
        )
        answers = []
        evidence = []
        for task in route.tasks:
            task.question = await self._gov.protect(task.question)
            passages = await pipeline.run(task.department, task.question, budget)
            if not passages:
                answers.append(Answer(status="insufficient_evidence"))
                continue
            with span(self._provider, "specialist", **{"app.department": task.department}):
                answer = await self._executor.run(
                    task.department,
                    {
                        "question": task.question,
                        "evidence": [p.model_dump(exclude={"corpus"}) for p in passages],
                    },
                    Answer,
                    budget,
                )
                validate_citations(answer, passages)
                answers.append(answer)
                evidence.extend(passages)
        if len(answers) == 1:
            final = answers[0]
        elif not any(a.claims for a in answers):
            final = Answer(status="insufficient_evidence")
        else:
            with span(self._provider, "synthesis"):
                final = await self._executor.run(
                    "synthesizer",
                    {"question": question, "department_answers": [a.model_dump() for a in answers]},
                    Answer,
                    budget,
                )
            # Synthesis can cite only source IDs already used in validated claims.
            claimed = {sid for a in answers for c in a.claims for sid in c.source_ids}
            validate_citations(final, [p for p in evidence if p.id in claimed])
        with span(self._provider, "output_screening"):
            text = await self._gov.protect(render(final), output=True)
        used = {sid for c in final.claims for sid in c.source_ids}
        sources = {p.id: p.model_dump(exclude={"text", "corpus"}) for p in evidence if p.id in used}
        return {
            "status": final.status,
            "answer": text,
            "claims": [c.model_dump() for c in final.claims],
            "sources": list(sources.values()),
            "departments": [t.department for t in route.tasks],
        }


# No cloud clients or credentials are created at import time.
app = CompanyRagApplication()
