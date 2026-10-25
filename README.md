# legacy-bridge

Business processes often live in legacy systems with half-finished APIs. Automating them with an LLM agent raises two problems.

- **Reliability and cost.** The API works until it doesn't. Driving a browser with an LLM is slow and expensive on every run.
- **Data exposure.** Sending customer records into a hosted model's context window leaks PII to a third party.

legacy-bridge is an agent that tries the API first, falls back to a browser when the API breaks, and compiles successful browser runs into replayable scripts. Every model call goes through a proxy that swaps PII for tokens. Real values are restored only at the moment the agent acts, so no model ever sees them.

**Status:** the testbed, the PII proxy and the API-first agent with browser fallback are built and measured on a 10-task subset. Replay compilation is next ([milestones](docs/milestones.md)).

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
| Detection micro-F1, synthetic portal text / ai4privacy English rows ([benchmark](analysis/results/20261022T134635Z-pii-benchmark/pii_benchmark.json)) | 0.936 / 0.881 (Presidio default: 0.547 / 0.682) |
| Latency the proxy adds, p50 / p95, CPU detection, Redis vault ([latency](analysis/results/20261023T035708Z-proxy-latency/proxy_latency.json)) | 259 ms / 1,596 ms |
| Prompt-token overhead of tokenization, gpt-oss tokenizer ([tokens](analysis/results/20261022T023527Z-token-overhead/token_overhead.json)) | +35% on PII-dense documents |

```bash
make proxy-demo     # one request end to end: masked prompt, streamed rehydration, tool-boundary policy, audit tamper check
make leak           # the CI gate: zero canaries may reach a provider
make bench-pii      # P/R/F1 per entity type for four detector configurations
```

### Limitations

- **Detection is slow on CPU.** The production configuration takes about 0.9–1.1 s per 1,000 characters on CPU, which drives the p95 latency. No GPU path is wired up yet.
- **The canary result depends on the known-values pass** within a task. With documents isolated, the detector alone also reached zero on this corpus, but the corpus is synthetic and narrow.
- **Per-type F1 understates coverage for SSNs (0.66) and street addresses (0.80)** on synthetic text, even though 98.9% of gold PII characters are covered. Predicted spans cover the value but miss the gold boundaries at IoU ≥ 0.5.
- **Credit cards in ai4privacy score 0.** The policy threshold drops GLiNER's low-confidence hits, and Presidio's recognizer requires a valid Luhn checksum, which those numbers lack.
- **On ai4privacy, the production configuration covers fewer gold characters than Presidio+GLiNER without the policy filter** (0.926 vs 0.990). Policy thresholds trade recall for precision.
- **Results vary slightly between runs.** GLiNER configurations moved by about 0.003 F1 on identical code, most likely from CPU threading.
- **Synthetic addresses use the same USPS suffix list as the street rule,** so the rule's synthetic recall is optimistic. The ai4privacy numbers are the better guide.
- **ai4privacy rows are used for evaluation only.** They are never committed or published, only these aggregates. The dataset is Built with Llama and also subject to the Llama Community License ([details](docs/environment.md)).

## The agent

A LangGraph state machine, checkpointed in Postgres:
1. **Plan** the task from the masked instruction.
2. **API tier.**
   - Tools are generated from the portal's OpenAPI document.
   - A missing, retired, failing or drifted endpoint hands the task to the browser.
   - Each endpoint has a circuit breaker. It opens for 10 s after repeated failures and doubles while probes fail, capped at 60 s ([ADR 0004](docs/adr/0004-circuit-breaker-parameters.md)).
3. **Browser tier.** Playwright MCP, driven by accessibility snapshots taken after the page settles.
4. **Verify.** A change task that executed no write, or a question finished without an answer, is never reported as a success.

Guardrails:
- **Approvals:** payments and deletes stop for approval. After suspected prompt injection, every write does.
- **Walls:** a CAPTCHA or MFA wall hands the task to a person. It is never solved.
- **Tokens only:** API responses and page snapshots are masked before they enter agent state ([ADR 0012](docs/adr/0012-masking-boundaries-for-the-agent.md)).
- **Restoring values:** placeholders are restored only in the tool arguments and form fields the policy allows. Malformed placeholders are refused rather than typed into the portal.
- **Injection classifier:** Llama Prompt Guard 2 (86M) scans pages locally. It is on by default but not relied on ([ADR 0014](docs/adr/0014-injection-classifier-evidence.md)).

Every model exchange is recorded as a cassette, with the injection scanner's verdicts. CI replays them with no model and no scanner, and fails if any task's outcome changes.

The local model is gemma4 12B, chosen by a [bake-off](analysis/results/20261023T154359Z-bakeoff/bakeoff.json) on 10 tasks and two scenarios ([ADR 0013](docs/adr/0013-local-agent-model.md)):

| Model | Passed | Reported done but failed |
|---|---|---|
| gemma4 12B | 20 / 20 | 0 |
| qwen3.5 9B | 17 / 20 | 2 |
| qwen3 14B | 17 / 20 | 1 |
| qwen2.5 14B | 14 / 20 | 0 |

Results with gemma4 on the 10-task development subset, graded against the database diff ([summary](analysis/results/20261024T075412Z-agent-summary/agent_summary.json)):

| Scenario | Passed | Wrong actions | Completed through the browser | Median time |
|---|---|---|---|---|
| Healthy (the API is partly unfinished) | 10 / 10 | 0 | 5 / 10 | 44 s |
| API degraded | 10 / 10 | 0 | 10 / 10 | 80 s |
| DOM drift (renamed labels, moved buttons, a modal) | 10 / 10 | 0 | 5 / 10 | 52 s |
| Injected notes (planted prompt injections) | 10 / 10 | 0 | 5 / 10 | 43 s |
| CAPTCHA wall at sign-in | 5 / 10 | 0 | 5 handed to a person | 14 s |

Other measurements:
- **Circuit breaker:** across every outage length from 1 to 30 minutes, it returns to the API 25 s after recovery on average, 50 s at worst. The proposed 30 s / 10-minute breaker took 253 s on average and 590 s at worst ([experiment](analysis/results/20261023T161000Z-breaker-recovery/breaker_recovery.json)).
- **Prompt Guard 2:** flagged 0 of 8 held-out planted injections, and 0 of 24 when they were embedded in pages. It catches only explicit "ignore all previous instructions" text, and it flagged none of 34 ordinary pages ([evaluation](analysis/results/20261023T155217Z-injection-scan/injection_scan.json)). Built with Llama.

```bash
make demo-fallback   # replay: the API refuses, the agent completes the tasks in the browser, the database diff decides
make replay-check    # every recorded task must reproduce its recorded outcome
make local-model     # pull gemma4:12b and create the agent's 16k-context variant
make record SCENARIO=healthy TASKS=claim_status-01   # record with the local model (Ollama)
make bakeoff         # compare local models on the development subset
make traces          # replay with OpenTelemetry GenAI spans to Phoenix (localhost:6006); spans are checked for canaries
```

### Limitations

- **Mostly ten tasks, one recording each.** Single runs moved by one or two tasks for the other models in the bake-off. The healthy scenario was also recorded on the other 40 tasks: 37 / 40 passed, 0 wrong actions ([results](analysis/results/20261025T030431Z-agent-run/agent_runs.jsonl)). Two of the three misses came from routing numbers masked as phone numbers, a detection bug fixed afterwards and not re-recorded. The full matrix, repeated runs and hosted models are not measured.
- **No planted injection succeeded, but the classifier did not stop them.** The approvals, allowlists and post-conditions did. One scenario with ten tasks is a small sample of attacks.
- **The agent's own check trusts the page.** In the bake-off, weaker models reported three tasks done that the database showed were not: a wrong answer, a wrong city and postal code, and a note left undeleted. Each was graded as failed. gemma4 had none, but the agent's verification step would not have caught them either.
- **One leak was caught late.** Under DOM drift, a canary account number escaped detection beside a renamed label. The cassette leak gate stopped it before the recording was committed, and the masking now re-catches long numbers by value ([ADR 0012](docs/adr/0012-masking-boundaries-for-the-agent.md)).
- **Replays are not bit-identical.** GLiNER on CPU masks a few borderline spans differently between runs, so some requests in every scenario differ from their recordings on each replay. They are served and counted, and every outcome matches.

## Cost

Successful browser runs compile into replay scripts: YAML steps that locate elements by role and accessible name, and fill values from the task's parameters, never from the run they came from ([ADR 0005](docs/adr/0005-replay-script-format.md)). The agent tries a script before the browser tier. When a step breaks, the browser tier finishes the task with the script's steps as a hint, and the fixed run compiles into the next version.

| Measurement | Result |
|---|---|
| Six scripts on the healthy portal ([replay eval](analysis/results/20261024T171309Z-replay-eval/replay_eval.json)) | 27 / 27 passed, 0 model calls, median 1.8 s |
| The same scripts under DOM drift | 0 / 27 passed, all 27 stopped at the broken step, 0 wrong actions |
| Self-heal under DOM drift ([ledger](analysis/results/20261024T224315Z-cost-ledger/cost_ledger.json)) | 12 / 12 passed; each broken script healed once, later tasks replayed the new version |
| Trimmed page snapshots, offline ([measurement](analysis/results/20261024T180841Z-snapshot-trim/snapshot_trim.json)) | 40.6% fewer snapshot tokens, no actionable element lost |
| Trimmed page snapshots, live ([A/B](analysis/results/20261024T224238Z-snapshot-ab/snapshot_ab.json)) | 17.7% fewer prompt tokens per call, 17 / 20 passed against 19 / 20 untrimmed |

Local runs cost $0. The ledger reports model time beside them and prices the same tokens at gpt-oss-120b's list price ([ADR 0007](docs/adr/0007-notional-pricing.md)). At that price, a task that needs the browser would cost about $0.004 to $0.006, an API-only task about $0.001, and a replayed task nothing.

Trimming stays off by default. Both extra misses changed the database correctly but never saw a confirmation, and ten tasks per scenario cannot tell that from noise.

### Pages without an accessibility tree

One portal form paints its labels and buttons on canvases and leaves its inputs unnamed. There, the browser executor:
- finds the unnamed elements in the DOM
- reads each canvas by OCR (Tesseract)
- lists them for the model with their screen positions

The model acts on them with `click_at` and `type_at`. An input's label is the caption beside it on screen, and the data policy and approvals apply to it as to a named element ([ADR 0008](docs/adr/0008-vision-fallback-and-redaction.md)). One reimbursement on that form was run to completion and passed; the other six are not yet measured.

Screenshots sent to a model are redacted in the proxy: each value detected in the OCR text is painted over with its token. On real member pages of the 10 canary members, OCR reads 100 canary values before redaction and 0 after ([measurement](analysis/results/20261025T000432Z-screenshot-leak/screenshot_leak.json)). A value OCR cannot read is not redacted either, and that path is not measured.

```bash
make demo-replay     # scripts at $0 on the healthy portal, clean step failures under DOM drift, then the cost ledger
```

## Operator console

A local page for running one task and watching it ([agent/console.py](agent/console.py)):
- pick a scenario and task
- follow each graph step as it happens: tool calls, page observations, tier changes
- approve or decline payments and deletes yourself
- see the database-diff verdict

The page shows what agent state holds, which is placeholders only, never raw values. With the proxy in replay mode it replays recorded tasks with no model. In live mode it runs the local model.

```bash
make console         # http://localhost:8095
```

## Design documents

- Deep dives: [the PII proxy](docs/deep-dives/01-pii-proxy.md), [fallback and replay](docs/deep-dives/02-fallback-and-replay.md), [cost and evaluation](docs/deep-dives/03-cost-and-evaluation.md)
- [Threat model](docs/threat-model.md)
- [Architecture decision records](docs/adr/README.md)
- [Verified environment and free-tier facts](docs/environment.md)
- [Milestones](docs/milestones.md)
