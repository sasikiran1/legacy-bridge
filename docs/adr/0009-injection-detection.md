# 0009. Injection detection on page content

- Status: Accepted. Contingent on Sasi obtaining gated access to Prompt Guard 2.
- Date: 2026-10-21

## Context

Portal content, especially the free-text notes field, is attacker-controlled (threat model T2). Before page text enters a prompt, we want a cheap classifier that flags instruction-like content, alongside structural defenses.

## Decision

- **Classifier:** `meta-llama/Llama-Prompt-Guard-2-86M`, run **locally** on CPU/GPU via `transformers`.
  - The 22M variant is the low-latency option. The choice between them is made on measured latency and attack success in M3.
  - Groq hosts both models, but we don't use that, because page text must not leave the host before masking.
- **Input:** the tokenized text the model would see. Chunked to the model's 512-token limit with 64-token overlap; the highest chunk score wins.
- **Threshold:** calibrated on a **dev split** of the injected-notes corpus and reported on a held-out **test split**, so the number isn't tuned on the data it's reported on.
- **On a flag:** the content is still shown to the model, but wrapped as quarantined data with a warning, and write actions in that step require approval. Blocking outright would let an attacker hide legitimate data by appending a trigger phrase.
- **Always on, flagged or not:**
  - spotlighting/datamarking: untrusted page text is wrapped in delimiters with an interleaved marker, and the system prompt states that marked text is data
  - action allowlist, approval interrupts, post-conditions (ADR 0003, threat model)

## Alternatives

- **Ungated Apache-2.0 injection classifiers on the Hub.** Avoid the gated-access form. To be evaluated only if Sasi declines the Llama license terms; the specific model and its license get verified at that point.
- **LLM-as-judge.** Costs a model call per page and is itself injectable. Rejected.
- **No classifier, structural defenses only.** A valid baseline, and it is measured as one: attack success with the classifier on vs. off.

## Consequences

- **Llama 4 Community License:**
  - "Built with Llama" attribution in the README and the report
  - Meta's Acceptable Use Policy applies
  - access requires Sasi's legal name, date of birth, and organization on the Hugging Face form; only Sasi submits it
- `HF_TOKEN` is needed to download the weights. It is not needed at run time once they're cached.
- Per the model card, adaptive attacks can bypass the classifier. It is one layer, and the report shows attack success with and without it.
