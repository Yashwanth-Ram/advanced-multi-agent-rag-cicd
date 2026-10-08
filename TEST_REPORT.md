# Verification report

Built on 2026-09-30. Target: CPython 3.12, Linux / Google Cloud Shell.

## Passed

| Check | Evidence |
|---|---|
| Clean dependency installation | Installed `requirements-dev.txt` in a new virtual environment |
| Dependency consistency | `python -m pip check`: no broken requirements |
| Offline test suite | 38 tests passed in the clean environment |
| Lint and format | Ruff check and format check passed |
| Full ADK execution boundary | Real ADK Runner + a local fixture model produced a validated structured result |
| SDK request compatibility | Model Armor/DLP request schemas, default template creation schemas, runtime source config and SDK entrypoints checked |
| Corpus isolation | Department/common bindings, denied whole routes, and no retrieval after denial |
| Retrieval handling | Query limits, duplicate removal, empty results, malicious documents before reranking |
| Answer provenance | Unknown source IDs and citation markup embedded in model prose rejected |
| Fail-closed behavior | Blocked input, screening outage, skipped/partial screening and call limits |
| Sensitive data before model calls | Mock redaction applied before routing input |
| Telemetry privacy | Exported attributes/events/names filtered; OTLP serialization excluded sensitive text |
| Cloud Trace encoding | Real Google exporter translated filtered spans into a mock client request with no sensitive content |
| Deployment packaging configuration | Dry-run validated source entrypoint and guarded async method |
| CI/CD configuration | Cloud Build YAML parsed; Bash scripts passed syntax checks |
| Evaluation dataset validation | Five routing records validated; three cases validated against native ADK EvalSet schema |

The test suite does not assert that Model Armor detects every attack. It asserts
that the application correctly enforces blocked/unavailable screening outcomes.
Source ID validation proves provenance; semantic groundedness needs live evaluation.

## Not run, as requested

- Live Gemini calls, routing accuracy or company-policy answer quality.
- Live RAG retrieval, Google reranking, actual corpus/embedding inspection.
- Creation or use of live Model Armor / DLP templates.
- Phoenix secret access, HTTP export delivery or console visibility.
- Live Cloud Trace / Cloud Logging / Monitoring verification.
- Agent Runtime deployment, remote invocation or Cloud Build trigger execution.
- Live ADK Eval or reviewed policy evaluation.

No cloud resources were created and no repository was pushed. The setup guide
contains the commands for these steps and expected outcomes.

## Dependency isolation finding

Installing Google's optional evaluation SDK (`google-cloud-aiplatform`) alongside
`google-cloud-agentplatform` overwrites shared `agentplatform` module files and can
remove `Client.runtimes`, despite `pip check` reporting no metadata conflicts.
The delivered main runtime lock excludes `google-cloud-aiplatform`. Native ADK Eval
has a standalone lock for an isolated `.eval-venv`; it is never installed into
Agent Runtime or PR CI. This separation is required for this SDK combination.

The primary lock has 92 exact runtime package pins. The dev lock adds exact test
and lint pins. The optional eval lock is independent and includes the Google
SDK evaluation dependencies, excluding the runtime SDK.

## SDK warnings observed

The installed SDKs emit warnings about experimental RAG APIs, Pydantic SDK
validators, the legacy Vertex environment flag, and the Cloud Trace exporter.
They did not fail checks. Your requested Vertex configuration is retained; future
SDK upgrades should re-resolve the entire lock and rerun checks. Model availability
is a live prerequisite checked by `scripts.preflight --models`, not established
by local package compatibility.

## Dev access model

Runtime IAM authorizes trusted developer callers. They share the server-configured
`DEV_ALLOWED_DEPARTMENTS`; there is no per-employee authorization gateway. Each
specialist's retrieval remains fixed to its own corpus plus common. No user/model
argument can select an arbitrary corpus. No web UI, ingestion, staging or production
environment is included.
