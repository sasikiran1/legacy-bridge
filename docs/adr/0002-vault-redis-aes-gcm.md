# 0002. Vault: Redis 8 with application-level AES-GCM

- Status: Accepted
- Date: 2026-10-21

## Context

Token→value mappings must be:

- readable by the proxy and the executor at low latency (several lookups per step)
- scoped to a task and expire when the task is done
- unreadable from a Redis dump, AOF file, or `MONITOR` session
- recoverable after a container restart, so a checkpointed LangGraph task can resume

Nothing may require a paid service.

## Decision

Redis 8.10 (`redis:8.10.2`). Values are encrypted in the application before they reach Redis.

**Per-task key:**
- `k = HKDF-SHA256(VAULT_MASTER_KEY, info="lb-vault-v1" || task_id)`

**Entries:**
- one hash per task, `vault:{task_id}`
- field = canonical token ID
- value = `nonce(12) || AES-256-GCM(k, nonce, json{type, value}, aad=task_id || token_id)`
- nonces are random

**TTL:**
- `EXPIRE vault:{task_id} VAULT_TASK_TTL_SECONDS` is set on creation and refreshed on each write
- the agent deletes the key when the task finishes

**Persistence:**
- AOF `everysec` on, so resumed tasks can still rehydrate
- only ciphertext is persisted

**Fail closed:** any vault error during masking blocks the outbound request. During rehydration it fails the tool call.

## Alternatives

**OpenBao 2.7 Transit engine** (open-source Vault fork, MPL-2.0).
- Advantages: keys never leave the server, built-in rotation, an audit device, and encryption-as-a-service.
- Costs here: another stateful service with an unseal workflow (dev mode isn't persistent) and an HTTP round trip per encrypt/decrypt. We'd still need a store for the ciphertexts, so Redis or Postgres stays either way.
- It is the right production answer for **key management**. Redis plus AES-GCM is the right answer for **mapping storage**. For a real deployment, use envelope encryption: OpenBao/KMS wraps per-task data keys, and Redis stores ciphertext.

**Postgres table with pgcrypto.** One less service. But TTL needs a sweeper, and pgcrypto does encryption in the database, which then sees plaintext.

**In-process dict.** Fastest, but doesn't survive restarts and can't be shared between the proxy and executor processes.

## Consequences

- The master key sits in `.env`. This is the weakest link and is documented in the threat model.
- Redis's tri-license (RSALv2/SSPLv1/AGPLv3) is fine for running it unmodified. Valkey is a drop-in, BSD-licensed alternative if that ever matters.
- Lookup latency is one Redis round trip plus an AES-GCM decrypt. It counts toward the proxy's added latency, which is measured in M2.
