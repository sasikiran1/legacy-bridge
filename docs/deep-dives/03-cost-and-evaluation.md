# Measuring an agent honestly on a $0 budget

legacy-bridge was built under three constraints: spend nothing, touch only systems we own, and publish no number that a script cannot reproduce from committed files. This write-up covers how the evaluation is set up, how cost is reported when the model is free, and the results that did not flatter the design.

## Ground truth is the database

Each of the 50 tasks carries an expected database diff. A run is graded by snapshotting the portal's Postgres before and after:
- **Missing** expected change: the task failed.
- **Any change outside the expectation:** a wrong action.

Ground truth never comes from the agent's own report. That mattered: in the model bake-off, weaker models reported three tasks done that the database showed were not (a wrong answer, a wrong city and postal code, a note left undeleted).

Every experiment writes `analysis/results/<run_id>/` with a manifest:
- git SHA, and whether the tree was dirty
- configuration
- model IDs and the Ollama version
- price date
- timestamps

The README and these write-ups cite those files directly. When the git history was rewritten before publishing, the commit IDs inside every manifest were remapped too, so each figure still points at the code that produced it.

## Reproducibility without paying for reruns

Model exchanges are recorded as cassettes keyed by task and call index, holding tokens only. CI replays every recorded task against the real portal with no model and fails if any outcome changes.

Two sources of non-determinism had to be handled rather than wished away:
- **GLiNER on CPU** masks a few borderline spans differently between runs, so replayed requests can differ from their recordings. They are still served, flagged and counted, and outcomes have matched.
- **Browser refs** depended on page-load timing until snapshots were taken only after the page settled.

## Cost when the model is free

Local runs on Ollama cost $0, and the ledger says so: it reports model seconds beside $0, never converted (ADR 0007). To compare tiers in money, each group also carries a counterfactual: the same input and output tokens priced at gpt-oss-120b's paid list price on the ledger date ($0.15 / $0.60 per million tokens on 2026-10-24).

| Path | Counterfactual cost per task |
|---|---|
| API tier only | about $0.001 |
| API, then browser | about $0.004 to $0.006 |
| Compiled replay script | $0, no model calls |

The cheapest token is the one not sent. Replay removes the model entirely for a known flow. Self-heal pays one browser run when the UI changes, then returns to replay at 2 to 3 calls.

## A result that went the other way

Trimming page snapshots removes wrapper elements, refs on non-actionable elements, the footer and console noise.
- **Offline:** 40.6% fewer snapshot tokens on every recorded page, and no actionable element lost.
- **Live:** the saving shrank to 17.7% fewer prompt tokens per call, because the system prompt and history don't shrink.
- **Success:** 17 of 20 tasks passed trimmed against 19 of 20 untrimmed, on the same Ollama version.

Both extra misses made the right database change but never saw a confirmation, so the agent's verify step marked them unverified. Ten tasks per scenario can't separate that from noise, so trimming stays off by default. That decision is in the README, not hidden in a branch.

## Other numbers that were less flattering than expected

- **Prompt Guard 2** flagged 0 of 8 held-out planted injections. It is kept but not counted on.
- **The proposed breaker of 30 s / 10 min** recovered in 253 s on average and 590 s at worst, against 25 s and 50 s for the chosen 10 s / 60 s.
  - The first measurement of the alternative looked better by coincidence: an outage that happened to end in phase with a probe.
  - A simulation over 175 outage lengths, validated against the real portal on 54 timelines, replaced it.
- **The proxy adds 259 ms at p50 and 1.6 s at p95** on CPU detection.

## Infrastructure lessons that cost real time

- **Ollama updated itself in the middle of a bake-off,** and its CUDA runner crashed under load. Manifests now record the runtime version, comparisons group by it, and long runs check the version before each scenario. The comparison was rerun on a single version.
- **Laptop standby froze two runs.** Results from those were discarded, not patched. Long runs now hold a system and display keep-awake.
- **The runner wrote results only at the end of a run,** so a pause discarded hours of recordings on two machines. It now saves after every task.
- **A second laptop doubled throughput** once both had identical model digests and the same token key. Without those, recordings from the two machines could not be combined.

## What is not measured

- **The full matrix of 50 tasks × 7 scenarios with repeated runs** was not completed. Most results are on the 10-task development subset, one recording per scenario. A second machine recorded the healthy scenario on the other 40 tasks: 37 / 40 passed, 0 wrong actions. Two of the three misses were the routing-number masking bug described in the proxy write-up.
- **Hosted models are not measured.** The routing and fallback chain to Groq and Gemini is built and unit-tested, but there were no API keys.
- **A vision-model arm** for the canvas-painted form is not measured. The text model, fed by OCR, completed the one task that was run.
