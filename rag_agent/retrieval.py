import asyncio
import hashlib
import re

import agentplatform
from agentplatform import types as platform_types
from google.cloud import discoveryengine_v1 as discovery
from google.genai import types

from rag_agent.observability import span
from rag_agent.schemas import Passage, Queries


def deduplicate(passages):
    seen = set()
    result = []
    for p in passages:
        key = (p.uri, re.sub(r"\s+", " ", p.text).strip())
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def source_id(corpus, uri, text):
    return "S" + hashlib.sha256((corpus + "\0" + uri + "\0" + text).encode()).hexdigest()[:16]


class CloudRetriever:
    def __init__(self, s):
        self.s = s
        self.ranking = discovery.RankServiceClient()

    async def search(self, corpus, query, k):
        project, location = corpus.split("/")[1], corpus.split("/")[3]

        # Retrieval uses the corpus project's location, including cross-project corpora.
        def invoke():
            client = agentplatform.Client(project=project, location=location)
            return client.rag.retrieve_contexts(
                vertex_rag_store=types.VertexRagStore(
                    rag_resources=[types.VertexRagStoreRagResource(rag_corpus=corpus)]
                ),
                query=platform_types.RagQuery(
                    text=query, rag_retrieval_config=types.RagRetrievalConfig(top_k=k)
                ),
            )

        response = await asyncio.to_thread(invoke)
        contexts = response.contexts.contexts if response.contexts else []
        result = []
        for ctx in contexts[:k]:
            content = ctx.text or (ctx.chunk.text if ctx.chunk else "")
            if not content or not content.strip():
                continue
            text = content[: self.s.max_passage_chars]
            uri = ctx.source_uri or ""
            result.append(
                Passage(
                    id=source_id(corpus, uri, text),
                    text=text,
                    uri=uri,
                    title=ctx.source_display_name or "Untitled source",
                    corpus=corpus,
                    first_page=ctx.chunk.page_span.first_page
                    if ctx.chunk and ctx.chunk.page_span
                    else None,
                    last_page=ctx.chunk.page_span.last_page
                    if ctx.chunk and ctx.chunk.page_span
                    else None,
                )
            )
        return result

    async def rank(self, question, passages, k):
        if not passages:
            return []
        response = await asyncio.to_thread(
            self.ranking.rank,
            request=discovery.RankRequest(
                ranking_config=f"projects/{self.s.project}/locations/global/rankingConfigs/default_ranking_config",
                model=self.s.ranker,
                query=question,
                top_n=k,
                records=[
                    discovery.RankingRecord(id=p.id, title=p.title, content=p.text)
                    for p in passages
                ],
            ),
            timeout=30,
            retry=None,
        )
        by_id = {p.id: p for p in passages}
        ids = [r.id for r in response.records]
        if any(x not in by_id for x in ids) or len(ids) != len(set(ids)):
            raise ValueError("Ranker returned unknown or duplicate IDs")
        return [by_id[x] for x in ids[:k]]


class RetrievalPipeline:
    def __init__(self, s, cloud, executor, governance, provider):
        self.s = s
        self.cloud = cloud
        self.executor = executor
        self.gov = governance
        self.provider = provider

    async def run(self, department, question, budget):
        corpora = self.s.permitted_corpora(department)
        with span(self.provider, "expansion", **{"app.department": department}):
            expansion = await self.executor.run("expander", {"question": question}, Queries, budget)
            queries = list(
                dict.fromkeys([question, *[q.strip() for q in expansion.queries if q.strip()]])
            )[: self.s.query_count]
        candidates = []
        with span(self.provider, "retrieval", **{"app.department": department}) as current:
            for query in queries:
                for corpus in corpora:
                    budget.retrieval()
                    candidates.extend(await self.cloud.search(corpus, query, self.s.per_query_k))
            candidates = deduplicate(candidates)
            # Bound concurrency and finish all screening before any ranking/model call.
            semaphore = asyncio.Semaphore(4)

            async def protected(text):
                async with semaphore:
                    return await self.gov.protect(text)

            texts = await asyncio.gather(
                *(protected(p.text) for p in candidates), return_exceptions=True
            )
            metadata_cache = {}
            for p, text in zip(candidates, texts, strict=True):
                if isinstance(text, BaseException):
                    raise text
                p.text = text
                for attr in ("title", "uri"):
                    value = getattr(p, attr)
                    if value and value not in metadata_cache:
                        metadata_cache[value] = await self.gov.protect(value)
                    setattr(p, attr, metadata_cache.get(value, ""))
            current.set_attribute("app.candidate_count", len(candidates))
        with span(self.provider, "rerank", **{"app.department": department}) as current:
            ranked = await self.cloud.rank(question, candidates, self.s.final_k)
            current.set_attribute("app.result_count", len(ranked))
            return ranked
