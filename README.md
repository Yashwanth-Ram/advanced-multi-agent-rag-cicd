# Advanced RAG: router + HR, management and technical agents

One development environment on Google Agent Runtime, built with Google ADK and
Cloud Build. Uses your four existing RAG corpora. No web app or ingestion jobs.

Start with **[the numbered setup guide](docs/SETUP.md)**. It explains each command,
its expected result, and how to test the deployment. See **[TEST_REPORT.md](TEST_REPORT.md)**
for the verification performed on this deliverable.

## What is included

- `company_router` selects one to three departments, or asks for clarification.
- HR, management and technical ADK agents answer with structured, cited claims.
- Fixed corpus bindings: each department can retrieve its own corpus + common.
- Query expansion (3), retrieval (10/query/corpus), deduplication, Google reranking (6).
- Explicit fail-closed Model Armor checks and Sensitive Data Protection redaction.
- Default dev templates, configured by a one-time setup script.
- Bounded calls, input sizes, output tokens and overall request deadline.
- Google Cloud Trace + Phoenix Cloud ADK/Gemini metadata tracing, safe structured logs.
- Offline tests, live routing evaluation and reviewed policy evaluation.
- PR CI with a separate unprivileged identity; trusted `main` deploys one dev runtime.
- Release archives, deployment manifests, setup commands and rollback instructions.

## Architecture

The router selects a structured route. Application code validates department access,
retrieves evidence, and invokes each specialist. Specialists have no dynamic tools:
retrieval is a controlled operation in the application rather than a tool selected
by the language model. A final agent combines claims for multi-department questions.

```
python -m scripts.query "What is the maternity leave policy?"
```

The deployed method is `async_query(question, history=None)`. The result includes
status, answer, departments, sources, request ID, release and call counts. Policies
are answered only from retrieved evidence; every claim must reference a valid
source ID. This validates provenance, not semantic truth; live evaluation checks
whether the claims are supported by the documents.

All runtime callers in this dev environment are trusted IAM principals and share
`DEV_ALLOWED_DEPARTMENTS`. The application accepts no permission or corpus arguments.
It does not implement per-employee authorization. Add an authenticated gateway and
server-side user permissions before making it an employee-facing service.

## Dependencies

Use **Python 3.12** in a fresh environment. `requirements.txt` pins the complete
runtime dependency set; `requirements-dev.txt` adds pinned test/lint packages.
`requirements.in` lists direct dependencies for future lock maintenance.
`requirements-eval.txt` is for a separate optional `.eval-venv`; it must never
be installed into the runtime environment because its Google SDK shares module
files with the runtime SDK.

Do not upgrade ADK, GenAI, Phoenix or OpenTelemetry independently inside this
project. Resolve a new complete lock and rerun all checks for upgrades.

## Scope and limits

Only dev is configured. CI gates offline security and correctness tests, not live
company-policy quality. Live evaluation and service verification are explicit user
commands. There are no production promotion, web hosting or document-import steps.
Parser, embedding and chunk variables from your supplied file are retained for
reference; they are not used to modify existing corpora.

Basic Model Armor sensitive-data findings may block a request before DLP redaction.
The DLP templates cover email, phone, credit cards and US SSNs; they are a starting
policy, not comprehensive coverage of all personal information or company secrets.

Metadata-only tracing excludes prompts, responses, retrieved passages, source URIs,
tool arguments and exception messages. Both destinations receive ADK/Gemini spans
via an isolated provider with two exporters. Native runtime telemetry remains under
Google's provider. No raw-content tracing/debug switch is included in this version.

The runtime package is custom because the guarded method surrounds the entire
multi-agent request; each component agent uses ADK. The optional `rag_agent.agent`
CLI entrypoint exposes only the router for ADK evaluation. Use `scripts.query` for
the full secure application. The pipeline does not expose an unguarded `AdkApp`.
