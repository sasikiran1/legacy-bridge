# Environment

Every version, license, price, and free-tier limit below was read from its source on **2026-10-21** unless a row says otherwise. Free tiers and prices change without notice, so re-verify before each recording or experiment run and update the date. Model IDs, prices, and limits live in [`config/models.yaml`](../config/models.yaml); this file records where they came from.

## Runtimes and platforms

| Component | Version | Notes | Source |
|---|---|---|---|
| JDK | 25 (LTS, GA 2025-09-16) | JDK 27 (GA 2026-09-15) is non-LTS; Boot 4.1 supports up to 26 | https://openjdk.org/projects/jdk/ |
| Spring Boot | 4.1.1 | 4.0.8 is the 4.0 maintenance line; 4.2.0-M2 is a milestone | https://github.com/spring-projects/spring-boot/releases |
| Spring Boot Java/Maven support | Java 17–26, Maven ≥ 3.6.3, Tomcat 11 | | https://docs.spring.io/spring-boot/system-requirements.html |
| springdoc-openapi | 3.1.1 | Built against Boot 4.1.0 | https://repo1.maven.org/maven2/org/springdoc/springdoc-openapi-starter-webmvc-ui/maven-metadata.xml |
| Testcontainers (Java) | 2.0.5 | 2.x prefixes modules: `testcontainers-postgresql` | https://repo1.maven.org/maven2/org/testcontainers/testcontainers/maven-metadata.xml |
| Flyway | 12.4.0 (Boot-managed; 13.11.1 is the latest release) | Boot 4.1.1 also manages Hibernate 7.4.5, Jackson 3.1.5, JUnit 6.0.3, Testcontainers 2.0.5, PostgreSQL JDBC 42.7.13 | https://repo1.maven.org/maven2/org/springframework/boot/spring-boot-dependencies/4.1.1/spring-boot-dependencies-4.1.1.pom |
| Maven | 3.9.16 via the wrapper (`portal/mvnw`, script-only, no committed jar) | Maven 4.0 is still at release candidates | https://repo1.maven.org/maven2/org/apache/maven/apache-maven/maven-metadata.xml |
| Docker base image | `eclipse-temurin:25-jdk` | | https://hub.docker.com/_/eclipse-temurin |
| PostgreSQL | 18.6 (`postgres:18.6`) | Supported until 2030-11-14 | https://www.postgresql.org/support/versioning/ |
| Redis | 8.10.2 (`redis:8.10.2`) | Tri-licensed RSALv2 / SSPLv1 / AGPLv3 since 8.0; we run it unmodified | https://github.com/redis/redis/releases, https://github.com/redis/redis/blob/unstable/LICENSE.txt |
| OpenBao | 2.7.1 | Considered in ADR 0002, not used | https://github.com/openbao/openbao/releases |
| Python | 3.14.9 | 3.15 is out (2026-10-09), but presidio and arize-phoenix cap at `<3.15`; 3.13 is security-only | https://www.python.org/downloads/ |
| uv | 0.12.9 (installed) | | local `uv --version` |
| Node.js | 26.5.0 (installed); build image `node:26.9.0` | Node 26 becomes LTS on 2026-10-28; Node 24 entered maintenance on 2026-10-20. `@playwright/mcp` needs ≥ 18; Vite 8 needs ≥ 22.12. | local `node --version`, https://github.com/nodejs/Release/blob/main/schedule.json |
| Ollama | 0.40.7 (installed) | qwen3-vl needs ≥ 0.12.7 | local `ollama --version` |
| GitHub Actions | free for public repos on standard hosted runners | Larger runners are always billed; don't use them | https://docs.github.com/en/billing/concepts/product-billing/github-actions |

## Python packages

Latest versions on PyPI as of the date above (`https://pypi.org/pypi/<name>/json`). Each package is added to `pyproject.toml` only when its milestone needs it, after approval.

| Package | Version | License | Note |
|---|---|---|---|
| langgraph | 1.2.16 | MIT | |
| langgraph-checkpoint-postgres | 3.1.1 | MIT | |
| mcp | 2.3.0 | MIT | New major line; don't use 1.x examples |
| playwright | 1.64.0 | Apache-2.0 | |
| presidio-analyzer / presidio-anonymizer | 2.2.364 | MIT | `[gliner]` extra; requires Python `<3.15` |
| gliner | 0.2.29 | Apache-2.0 | Pins `transformers<5.17.0` (latest is 5.20.2) |
| transformers | 5.16.1 (resolved; capped by gliner) | Apache-2.0 | |
| torch | 2.14.1+cpu from https://download.pytorch.org/whl/cpu | BSD-3-Clause | CPU-only wheel; the PyPI default bundles CUDA |
| spacy | 3.8.16 | MIT | Presidio's default NLP engine |
| en_core_web_lg | 3.8.0 (wheel from github.com/explosion/spacy-models releases) | MIT | Presidio's default spaCy model |
| huggingface-hub | 1.33.0 | Apache-2.0 | Model and dataset downloads |
| fastapi | 0.159.1 | MIT | |
| uvicorn | 0.54.0 | BSD-3-Clause | |
| sse-starlette | 3.5.0 | BSD-3-Clause | |
| httpx | 0.28.1 | BSD-3-Clause | |
| pydantic | 2.14.1 | MIT | |
| redis (redis-py) | 8.1.0 | MIT | |
| psycopg[binary] | 3.3.6 | LGPL-3.0-only | Post-condition snapshots; also what langgraph-checkpoint-postgres uses. Used unmodified as a library. |
| types-pyyaml | 6.0.12.x | Apache-2.0 | Stubs for mypy strict |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | AES-GCM for the vault |
| Faker | 40.43.0 | MIT | |
| PyYAML | 6.0.3 | MIT | |
| Pillow | 13.0.0 (checked 2026-10-24) | MIT-CMU | Screenshot decoding and redaction boxes in the proxy (ADR 0008) |
| tenacity | 9.2.1 | Apache-2.0 | |
| langchain-openai | 1.7.2 | MIT | OpenAI-compatible client for Groq, Gemini, Ollama, via the proxy |
| opentelemetry-sdk / exporter-otlp-proto-http | 1.45.1 | Apache-2.0 | |
| arize-phoenix | 20.24.0 | Elastic License 2.0 | ADR 0006 |
| polars | 2.0.0 | MIT | New major line |
| ruff | 0.17.1 | MIT | |
| mypy | 2.4.0 | MIT | |
| pytest | 9.1.1 | MIT | |
| pytest-asyncio | 1.4.0 | Apache-2.0 | |
| hypothesis | 6.170.5 | MPL-2.0 | |

## Portal frontend packages

Latest versions on npm as of the date above (`https://registry.npmjs.org/<name>/latest`). Exact versions are locked in `portal/web/package-lock.json`. See ADR 0010.

| Package | Version | License | Note |
|---|---|---|---|
| react / react-dom | 19.3.1 | MIT | |
| vite | 8.3.5 | MIT | Published 2026-10-21; if it misbehaves, drop to the previous patch |
| @vitejs/plugin-react | 6.1.2 | MIT | |
| typescript | 7.0.2 | Apache-2.0 | Native (Go) compiler line |
| react-router | 8.5.0 | MIT | Needs Node ≥ 22.22 |
| @types/react / @types/react-dom | 19.3.0 | MIT | Type definitions for React 19 |

## Containers

| Image | Pin | Source |
|---|---|---|
| `grafana/otel-lgtm` | `0.37.0` (2026-10-16, digest `sha256:57b5ab1ca661…`); bundles a collector, Prometheus, Tempo, Loki, and Grafana | https://hub.docker.com/r/grafana/otel-lgtm |
| `arizephoenix/phoenix` | `version-20.24.0` | https://hub.docker.com/r/arizephoenix/phoenix |
| `postgres` | `18.6` | https://hub.docker.com/_/postgres |
| `node` (frontend build stage only) | `26.9.0` | https://hub.docker.com/_/node |
| `maven` (portal build stage, `make portal-test`) | `3.9.16-eclipse-temurin-25-noble` | https://hub.docker.com/_/maven |
| `eclipse-temurin` (portal runtime) | `25-jre` | https://hub.docker.com/_/eclipse-temurin |
| `mcr.microsoft.com/playwright/python` (testbed base) | `v1.64.0-noble` | https://mcr.microsoft.com/v2/playwright/python/tags/list |
| `astral/uv` (copied into the testbed image) | `0.12.9` | https://hub.docker.com/r/astral/uv |

System packages installed into the Python image with apt (Ubuntu 24.04 noble archive, checked 2026-10-24 with `apt-cache policy`):

| Package | Version | License | Note |
|---|---|---|---|
| `tesseract-ocr` | 5.3.4-1build5 | Apache-2.0 | OCR word boxes for screenshot redaction (ADR 0008); https://github.com/tesseract-ocr/tesseract |
| `tesseract-ocr-eng` | 1:4.1.0-2 | Apache-2.0 | English traineddata; https://github.com/tesseract-ocr/tessdata_fast |

## CI actions

| Action | Version | Source |
|---|---|---|
| actions/checkout | v7 (7.0.1) | https://github.com/actions/checkout/releases |
| actions/setup-java | v6 (6.1.0) | https://github.com/actions/setup-java/releases |
| actions/setup-node | v7 (7.1.0) | https://github.com/actions/setup-node/releases |
| astral-sh/setup-uv | v10 (10.3.1) | https://github.com/astral-sh/setup-uv/releases |
| `redis` | `8.10.2` | https://hub.docker.com/_/redis |

## Models and datasets

| Artifact | License / access | Constraints that affect us | Source |
|---|---|---|---|
| `urchade/gliner_multi_pii-v1` | Apache-2.0 | Fine-tuned from `gliner_multi-v2.1` on synthetic PII | https://huggingface.co/urchade/gliner_multi_pii-v1 |
| `microsoft/mdeberta-v3-base` (tokenizer pulled by GLiNER) | MIT | Downloaded on first GLiNER load | https://huggingface.co/microsoft/mdeberta-v3-base |
| `openai/gpt-oss-20b` (tokenizer files only) | Apache-2.0 | Used to count LLM tokens in analysis/token_overhead.py | https://huggingface.co/openai/gpt-oss-20b |
| `meta-llama/Llama-Prompt-Guard-2-86M` / `-22M` | Llama 4 Community License, **gated** | Access form asks for legal name, DOB, and organization; Sasi submits it. 512-token input, so chunk. Binary benign/malicious. "Built with Llama" attribution. Run locally; never send page text to Groq's hosted copy. | https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M |
| `qwen3-vl:8b` (Ollama) / `Qwen/Qwen3-VL-8B-Instruct` | Apache-2.0 | 6.2 GB, fits the 12 GB GPU; documents GUI understanding and OCR | https://ollama.com/library/qwen3-vl, https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct |
| `qwen2.5:14b` (Ollama), local variant `qwen2.5-14b-16k` / `Qwen/Qwen2.5-14B-Instruct` | Apache-2.0 | 14.8B, Q4_K_M. The local variant sets `num_ctx 16384` (Ollama's context default is too short for page snapshots). Agent model for the M3 recordings. | https://ollama.com/library/qwen2.5, https://huggingface.co/Qwen/Qwen2.5-14B-Instruct |
| `qwen3:14b` (Ollama) / `Qwen/Qwen3-14B` | Apache-2.0, not gated (checked 2026-10-22) | 14.8B, Q4_K_M, 9.3 GB. Tools and thinking. M3 bake-off candidate, run as a `num_ctx 16384` variant. | https://ollama.com/library/qwen3, https://huggingface.co/Qwen/Qwen3-14B |
| `qwen3.5:9b` (Ollama) / `Qwen/Qwen3.5-9B` | Apache-2.0, not gated (checked 2026-10-22) | 8.95B, Q4_K_M, 6.6 GB. Tools, thinking and vision. M3 bake-off candidate, run as a `num_ctx 16384` variant. | https://ollama.com/library/qwen3.5, https://huggingface.co/Qwen/Qwen3.5-9B |
| `gemma4:12b` (Ollama) | Apache-2.0 per the Ollama license layer (checked 2026-10-22); Gemma 4 moved from Google's custom license to Apache-2.0 | 11.9B, Q4_K_M, 8.0 GB. Tools and vision. M3 bake-off candidate, run as a `num_ctx 16384` variant. | https://ollama.com/library/gemma4 |
| `ai4privacy/open-pii-masking-500k-ai4privacy` | Metadata: CC BY 4.0. README: **also subject to the Llama Community License** | Eval only. Download at run time into `analysis/data/raw/` (gitignored). Never commit or publish rows; publish aggregate metrics only. Show "Built with Llama" and the citation on the report page. The README says redistribution "beyond the permitted scope may require explicit written permission". ~580k rows, 8 languages. | https://huggingface.co/datasets/ai4privacy/open-pii-masking-500k-ai4privacy |

## Hosted LLM providers

### Groq

- **Free chat models:** `openai/gpt-oss-20b` and `openai/gpt-oss-120b` (production), plus `qwen/qwen3.8-27b` (preview: "evaluation purposes only").
- **Llama chat models** (`llama-3.1-8b-instant`, `llama-3.3-70b-versatile`) are listed as "Contact Sales", with no public price and no free limits. Don't use them.
- **Published limits** for each of the three chat models: 30 RPM, 1K RPD, 8K TPM, 200K TPD.
  - The page also says the shown limits are "base limits for the Developer plan" and puts free and Developer in separate tabs. **Sasi confirms the free numbers in the console** and updates `models.yaml`.
- **Groq also hosts Prompt Guard 2.** We don't use it, because page text must stay local.
- **Sources:**
  - https://console.groq.com/docs/models
  - https://console.groq.com/docs/rate-limits

### Google AI Studio (Gemini API)

- **Current free-tier text models** (code strings from the models page): `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3-flash-preview`.
- **The 2.5 models** are "legacy, access limited to past users", so don't plan on them.
- **Per-model free limits are no longer in the public docs.** The rate-limit page defers to the AI Studio console. **Sasi copies them from https://aistudio.google.com/rate-limit** into `models.yaml`.
- **Unpaid-services terms** (effective 2026-03-23, modified 2026-04-28):
  - inputs and outputs are used "to provide, improve, and develop" Google products
  - "human reviewers may read, annotate, and process your API input and output"
  - "Do not submit sensitive, confidential, or personal information to the Unpaid Services"

  This is why only tokenized synthetic text goes to Gemini.
- **Prices change on 2027-01-01** for `gemini-3.8-flash` and `gemini-3.6-flash` (input $0.75 → $1.50, output $3.75 → $7.50 per 1M). `models.yaml` stores effective-dated prices.
- **Thinking tokens bill as output** on the paid tier, and the cost ledger counts them.
- **Sources:**
  - https://ai.google.dev/gemini-api/docs/models (updated 2026-10-21)
  - https://ai.google.dev/gemini-api/docs/pricing (updated 2026-10-19)
  - https://ai.google.dev/gemini-api/docs/rate-limits (updated 2026-10-15)
  - https://ai.google.dev/gemini-api/terms

## Observability conventions

The OTel GenAI semantic conventions moved to https://github.com/open-telemetry/semantic-conventions-genai, which has no tagged release yet. We pin commit `9ffc7e00c639be7712e544e3e54331e885dccf84` (2026-10-21) in ADR 0006. Phoenix ≥ 15.10.0 converts `gen_ai.*` span attributes to OpenInference on ingest (https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion).

## Development host

- Windows 11, Intel Core Ultra 9 275HX, 31 GB RAM
- NVIDIA RTX 5070 Ti Laptop GPU, 12 GB VRAM (driver 581.80)
- about 75 GB free disk
- Docker Desktop with the WSL2 backend; Ubuntu in WSL for `make`
- not installed on the host: `make` (use WSL), `gh`, Tesseract (it lives in the proxy image)
- **Windows Application Control (Smart App Control) blocks unsigned native binaries.** Verified blocked:
  - Vite's `rolldown` binding
  - the `mypy` and `pytest` launcher executables
  - mypy's compiled modules
  - psycopg's binary driver

  Pure-Python code, `ruff`, `uv`, and `tsc` run. Everything else runs in containers (`make` targets, the `testbed` Compose service). Don't weaken the policy to work around it.
- WSL Ubuntu 26.04 has Python 3.14.4, Docker (Desktop integration), and uv 0.12.9 (user install). `make` and Playwright's system libraries need `sudo apt`, which Sasi runs.
