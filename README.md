# legacy-bridge

Business processes often live in legacy systems with half-finished APIs. Automating them with an LLM agent raises two problems.

- **Reliability and cost.** The API works until it doesn't. Driving a browser with an LLM is slow and expensive on every run.
- **Data exposure.** Sending customer records into a hosted model's context window leaks PII to a third party.

legacy-bridge is an agent that tries the API first, falls back to a browser when the API breaks, and compiles successful browser runs into replayable scripts. Every model call goes through a proxy that swaps PII for tokens. Real values are restored only at the moment the agent acts, so no model ever sees them.

**Status:** the testbed, the PII proxy and the API-first agent with browser fallback are built. Replay compilation is not built yet ([milestones](docs/milestones.md)).

## The testbed

The agent's target is a mock member-services portal in this repo:
- a Spring Boot 4 backend with a React front end
- 200 synthetic members (Faker, seeded, with planted canary values)
- 50 business tasks

The portal's public API is partly unfinished even when nothing is broken, which is typical of legacy systems. A chaos admin endpoint degrades it further:
- removed, retired, or failing endpoints
- schema drift
- renamed and moved UI elements
- a CAPTCHA wall
- a page with no accessibility tree
- notes carrying prompt-injection text

Each task has:
- an **expected database diff**. Anything else that changes counts as a wrong action.
- a **scripted Playwright oracle** (no LLM) proving the task can be done through the UI.

Requirements: Docker. On Windows, also `make` in WSL.

```bash
cp .env.example .env    # then set POSTGRES_PASSWORD, PORTAL_ADMIN_TOKEN, PORTAL_TEST_USER_PASSWORD
make up                 # portal on http://localhost:8080, API docs at /swagger-ui.html
make probe              # public-API outcome per operation and chaos scenario
make oracles            # 50 tasks x 7 scenarios through the UI
```

## The PII proxy

The proxy is an OpenAI-compatible `/v1/chat/completions` endpoint that every model call goes through.

**Masking.** Each request is masked before it leaves:
- **JSON keys** the policy knows (`ssn`, `dateOfBirth`, …) are tokenized by key.
- **Free text** goes through Presidio, a GLiNER PII model, and rules for the portal's IDs.
- **Values seen earlier** in the same task are re-caught wherever they reappear.

Each value becomes a typed token like `[[PERSON:3cb2b488d0]]`, deterministic within a task. The token→value map sits in Redis, AES-GCM encrypted.

**Rehydration.** Real values come back only in two places:
- in tool arguments, at the moment the agent acts, and only where the policy allows that type in that argument
- in operator-facing output, on request, including SSE streams where tokens are split across chunks

**Audit.** Every mask and unmask lands in a hash-chained audit log. If detection or the vault is unavailable, nothing is forwarded.

Results, each linked to the file it comes from:

| | Result |
|---|---|
| Canary values in recorded provider payloads, production detector ([leak check](analysis/results/20261022T120500Z-leak-check/leak.json)) | 0 of 125 per agent session; 0 of 125 with each document isolated |
| Same check with Presidio's default analyzer | 23 / 76 of 125 leaked |
| Detection micro-F1, synthetic portal text / ai4privacy English rows ([benchmark](analysis/results/20261022T025101Z-pii-benchmark/pii_benchmark.json)) | 0.907 / 0.854 (Presidio default: 0.547 / 0.682) |
| Latency the proxy adds, p50 / p95, CPU detection, Redis vault ([latency](analysis/results/20261022T023532Z-proxy-latency/proxy_latency.json)) | 262 ms / 1,779 ms |
| Prompt-token overhead of tokenization, gpt-oss tokenizer ([tokens](analysis/results/20261022T023527Z-token-overhead/token_overhead.json)) | +35% on PII-dense documents |

```bash
make proxy-demo     # one request end to end: masked prompt, streamed rehydration, tool-boundary policy, audit tamper check
make leak           # the CI gate: zero canaries may reach a provider
make bench-pii      # P/R/F1 per entity type for four detector configurations
```

### Limitations

- **Detection is slow on CPU.** GLiNER costs about 2 s per 1,000 characters on CPU, which drives the p95 latency. No GPU path is wired up yet.
- **The canary result depends on the known-values pass** within a task. With documents isolated, the detector alone also reached zero on this corpus, but the corpus is synthetic and narrow.
- **Per-type F1 understates coverage for street addresses (0.62) and SSNs (0.66)** on synthetic text, even though 99.9% of gold PII characters are covered. GLiNER's wider spans (street plus city plus ZIP) merge with the rules' spans and miss the gold boundaries at IoU ≥ 0.5.
- **Credit cards in ai4privacy score 0.** The policy threshold drops GLiNER's low-confidence hits, and Presidio's recognizer requires a valid Luhn checksum, which those numbers lack.
- **On ai4privacy, the production configuration covers fewer gold characters than Presidio+GLiNER without the policy filter** (0.934 vs 0.991). Policy thresholds trade recall for precision.
- **Results vary slightly between runs.** GLiNER configurations moved by about 0.003 F1 on identical code, most likely from CPU threading.
- **Synthetic addresses use the same USPS suffix list as the street rule,** so the rule's synthetic recall is optimistic. The ai4privacy numbers are the better guide.
- **ai4privacy rows are used for evaluation only.** They are never committed or published, only these aggregates. The dataset is Built with Llama and also subject to the Llama Community License ([details](docs/environment.md)).

## The agent

A LangGraph state machine, checkpointed in Postgres:
1. **Plan** the task from the (masked) instruction.
2. **API tier.** Tools are generated from the portal's OpenAPI document. Each endpoint has a circuit breaker. A missing, retired, failing or drifted endpoint hands the task to the browser.
3. **Browser tier.** Playwright MCP, driven by accessibility snapshots.
4. **Verify.** A change task that executed no write, or a question finished without an answer, is never reported as a success.

Guardrails:
- Payments and deletes stop for approval. After suspected prompt injection, every write does.
- A CAPTCHA or MFA wall hands the task to a person; it is never solved.
- Agent state holds tokens only: API responses and page snapshots are masked before they enter it ([ADR 0012](docs/adr/0012-masking-boundaries-for-the-agent.md)).
- Placeholders are restored only in the tool arguments and form fields the policy allows. Malformed placeholders are refused rather than typed into the portal.

Every model exchange is recorded as a cassette. CI replays them with no model and fails if any task's outcome changes.

Results on a 10-task development subset, local model qwen2.5-14b, graded against the database diff ([summary](analysis/results/20261022T134804Z-agent-summary/agent_summary.json)):

| Scenario | Passed | Wrong actions | Completed through the browser | Median time |
|---|---|---|---|---|
| Healthy (the API is partly unfinished) | 9 / 10 | 0 | 5 / 10 | 34 s |
| API degraded | 7 / 10 | 0 | 10 / 10 | 130 s |

```bash
make demo-fallback   # replay: the API refuses, the agent completes the tasks in the browser, the database diff decides
make replay-check    # every recorded task must reproduce its recorded outcome
make record SCENARIO=healthy TASKS=claim_status-01   # record with the local model (Ollama)
```

### Limitations

- **Ten tasks and two scenarios so far.** The other five scenarios, the full task set and hosted models are not measured yet.
- **No failure changed anything it should not have.**
  - In two of the four failures, the database change was correct, but the agent could not confirm it in the page and did not claim success.
  - In the other two, the expected change is missing from the database: the note was not added, or the phone number does not match.
- **The local model is the weak link.** In the recordings, qwen2.5-14b sometimes:
  - writes tool calls as text
  - drops placeholder brackets
  - drifts out of English

  A local-model comparison is planned.
- **Replays are not bit-identical.** GLiNER on CPU masks a few borderline spans differently between runs, so some replayed requests differ from their recordings. They are served and counted; outcomes have matched.

## Design documents

- [Threat model](docs/threat-model.md)
- [Architecture decision records](docs/adr/README.md)
- [Verified environment and free-tier facts](docs/environment.md)
- [Milestones](docs/milestones.md)
