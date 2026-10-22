# legacy-bridge

Business processes often live in legacy systems with half-finished APIs. Automating them with an LLM agent raises two problems.

- **Reliability and cost.** The API works until it doesn't. Driving a browser with an LLM is slow and expensive on every run.
- **Data exposure.** Sending customer records into a hosted model's context window leaks PII to a third party.

legacy-bridge is an agent that tries the API first, falls back to a browser when the API breaks, and compiles successful browser runs into replayable scripts. Every model call goes through a proxy that swaps PII for tokens. Real values are restored only at the moment the agent acts, so no model ever sees them.

**Status:** the testbed is built. The agent and the PII proxy are not built yet ([milestones](docs/milestones.md)).

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

## Design documents

- [Threat model](docs/threat-model.md)
- [Architecture decision records](docs/adr/README.md)
- [Verified environment and free-tier facts](docs/environment.md)
- [Milestones](docs/milestones.md)
