from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from proxy.audit import Action, AuditLog, verify
from proxy.vault import MemoryVault, RedisVault, Sealer, TokenCollision, VaultEntry, VaultUnavailable
from tests.proxy.fakes import VAULT_KEY

ENTRY = VaultEntry(entity_type="US_SSN", value="123-45-6789")
TOKEN = "[[US_SSN:0123456789]]"


async def test_round_trip_and_ciphertext_at_rest() -> None:
    vault = MemoryVault(Sealer(VAULT_KEY))
    await vault.put("t1", TOKEN, ENTRY)
    assert await vault.get("t1", TOKEN) == ENTRY
    assert b"123-45-6789" not in vault.raw("t1")[TOKEN]


async def test_ciphertext_is_bound_to_task_and_token() -> None:
    sealer = Sealer(VAULT_KEY)
    blob = sealer.seal("t1", TOKEN, ENTRY)
    with pytest.raises(VaultUnavailable):
        sealer.open("t2", TOKEN, blob)
    with pytest.raises(VaultUnavailable):
        sealer.open("t1", "[[US_SSN:9999999999]]", blob)
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(VaultUnavailable):
        sealer.open("t1", TOKEN, tampered)


async def test_same_token_different_value_is_a_collision() -> None:
    vault = MemoryVault(Sealer(VAULT_KEY))
    await vault.put("t1", TOKEN, ENTRY)
    await vault.put("t1", TOKEN, ENTRY)
    with pytest.raises(TokenCollision):
        await vault.put("t1", TOKEN, VaultEntry(entity_type="US_SSN", value="987-65-4321"))


@pytest.mark.redis
async def test_redis_vault_round_trip_ttl_and_collision() -> None:
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(os.environ["REDIS_URL"])
    vault = RedisVault(client, Sealer(VAULT_KEY), ttl_seconds=120)
    task = f"test-{os.getpid()}"
    try:
        await vault.put(task, TOKEN, ENTRY)
        assert await vault.get(task, TOKEN) == ENTRY
        assert 0 < await client.ttl(f"vault:{task}") <= 120
        raw = await client.hget(f"vault:{task}", TOKEN)
        assert raw is not None
        assert b"123-45-6789" not in raw
        with pytest.raises(TokenCollision):
            await vault.put(task, TOKEN, VaultEntry(entity_type="US_SSN", value="987-65-4321"))
    finally:
        await vault.drop(task)
        await client.aclose()


@pytest.mark.redis
async def test_unreachable_redis_raises_vault_unavailable() -> None:
    import redis.asyncio as aioredis

    vault = RedisVault(
        aioredis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2), Sealer(VAULT_KEY), 60
    )
    with pytest.raises(VaultUnavailable):
        await vault.put("t", TOKEN, ENTRY)


async def _log(tmp_path: Path, n: int) -> Path:
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(n):
        await log.record(f"t{i % 2}", Action.MASK, "PERSON", f"[[PERSON:{i:010x}]]", "groq")
    return log.path


async def test_chain_verifies_and_survives_reopening(tmp_path: Path) -> None:
    path = await _log(tmp_path, 5)
    reopened = AuditLog(path)
    await reopened.record("t9", Action.UNMASK, "PERSON", "[[PERSON:00000000ff]]", "client")
    result = verify(path)
    assert result.ok
    assert result.entries == 6
    assert result.head == reopened.head


@pytest.mark.parametrize("tamper", ["edit", "delete", "reorder"])
async def test_tampering_is_detected(tmp_path: Path, tamper: str) -> None:
    path = await _log(tmp_path, 5)
    lines = path.read_text(encoding="utf-8").splitlines()
    match tamper:
        case "edit":
            entry = json.loads(lines[2])
            entry["destination"] = "client"
            lines[2] = json.dumps(entry)
        case "delete":
            del lines[2]
        case "reorder":
            lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = verify(path)
    assert not result.ok
    assert result.error is not None
