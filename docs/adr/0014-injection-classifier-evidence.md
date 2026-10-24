# 0014. Injection classifier: 86M, on by default locally, not relied on

- Status: Accepted. Settles the model choice left open in [ADR 0009](0009-injection-detection.md); the rest of 0009 stands.
- Date: 2026-10-23

## Context

ADR 0009 chose Llama Prompt Guard 2 and left the choice between the 86M and 22M models to measurement. Gated access arrived in M3. The two models were compared on:
- **attacks:** the portal's 16 planted injections, alone and embedded in real page snapshots
- **benign texts:** every page and API response the agent read in the committed healthy recordings

The threshold was calibrated on the dev split and reported on the test split ([results](../../analysis/results/20261023T155217Z-injection-scan/injection_scan.json)):

| | 86M | 22M |
|---|---|---|
| Calibrated threshold | 0.95 | 0.95 |
| Test injections flagged, alone / in a page | 0 of 8 / 0 of 24 | 0 of 8 / 0 of 24 |
| Dev injections flagged, alone / in a page | 1 of 8 / 3 of 24 | 1 of 8 / 0 of 24 |
| Benign texts flagged, dev / test | 0 of 20 / 0 of 14 | 0 of 20 / 0 of 14 |
| Median CPU time per 1,000 characters | 248 ms | 100 ms |

The only injection either model flags is the explicit "Ignore all previous instructions…", which sits in the dev split. The planted injections are mostly authority- or business-styled, such as "Per compliance ticket 4471 the assistant must…". Both models score these near zero, whether alone or embedded in a page.

## Decision

1. **Use the 86M model.** It is the only one that still flags the explicit injection when it is embedded in a page. It costs about 2.5 times the 22M model's CPU time.
2. **On by default for local runs** (`INJECTION_SCANNER=promptguard` in `.env.example`):
   - It flagged no benign text, so it never added a needless approval stop.
   - A flag still raises the approval requirement on writes.
3. **Not relied on for protection.** The defenses against the planted injections are the structural ones:
   - spotlighting
   - the action allowlist
   - approvals for payments and deletes
   - the post-condition diff

   The injected-notes scenario measures whether those hold.
4. **Off in CI** (`compose.yaml` default `none`), because the model is gated and CI has no token. A replay check skips tasks recorded with a different scanner and reports how many, rather than failing or passing them silently.

## Alternatives

- **22M.** Faster, but it misses even the explicit injection inside a page. Rejected.
- **Off.** It costs about a quarter of a second per 1,000 characters of page text and has caught one class of attack (explicit overrides) without false positives here. Kept on, as defense in depth.
- **Tune the threshold lower to catch more.** With the explicit injection set aside, the 86M model's highest attack score is 0.058, and only 4 of the other 60 attack texts score above the highest benign score of 0.028. A threshold near 0.03 would catch those 4 on this corpus, but it sits a few hundredths above benign noise, measured on only 34 benign texts. Rejected as too fragile.
- **A different classifier** (for example the ungated ProtectAI DeBERTa v2). Not evaluated yet; a candidate for the M5 comparison.

## Consequences

- **Results must say the scanner barely helps.** Injection results are reported per defense, and the headline is the structural defenses' attack success rate, not the classifier's.
- **Attribution:** Llama 4 Community License attribution ("Built with Llama") is shown wherever the scanner is used in published results.
- **Small benign set:** 34 texts. The false-positive rate is not established beyond this corpus.

## Amendment (2026-10-24)

Decision 4 is changed. Skipping tasks recorded with a different scanner would have skipped every recording, because recordings run with the scanner and CI runs without it. Instead:
- each recording stores the scanner's verdicts as `scans.json` beside its cassettes, and a replay serves them in order, so a replay never needs the gated model
- recordings made before verdicts were stored replay as never flagged, which is exact for them: none was quarantined
- the cassette index now records `quarantined`, and a replay check compares it
