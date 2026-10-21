# legacy-bridge

Business processes often live in legacy systems with half-finished APIs. Automating them with an LLM agent raises two problems.

- **Reliability and cost.** The API works until it doesn't. Driving a browser with an LLM is slow and expensive on every run.
- **Data exposure.** Sending customer records into a hosted model's context window leaks PII to a third party.

legacy-bridge is an agent that tries the API first, falls back to a browser when the API breaks, and compiles successful browser runs into replayable scripts. Every model call goes through a proxy that swaps PII for tokens. Real values are restored only at the moment the agent acts, so no model ever sees them.

The target is a mock legacy portal in this repo, with switches that break its API and DOM on demand. All personal data is synthetic.

**Status:** design phase. The verified environment, threat model, and initial decisions are written. No code yet.

- [Threat model](docs/threat-model.md)
- [Architecture decision records](docs/adr/README.md)
- [Verified environment and free-tier facts](docs/environment.md)
- [Milestones](docs/milestones.md)
