"""Token→value vault (ADR 0002): values are AES-GCM encrypted in the application before they reach Redis.

One Redis hash per task (`vault:{task_id}`), field = rendered token, value = nonce || ciphertext.
The per-task key comes from HKDF over the master key, and the AAD binds each ciphertext to its task and token,
so a ciphertext copied to another task or field fails to decrypt.
"""

from __future__ import annotations

import json
import os
from typing import Protocol, cast

import redis.asyncio as aioredis
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError


class VaultEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_type: str
    value: str


class VaultUnavailable(Exception):
    """The vault cannot be reached or read. Callers must fail closed."""


class TokenCollision(Exception):
    """Two different values produced the same short token within one task (ADR 0001)."""


class Sealer:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("VAULT_MASTER_KEY must decode to 32 bytes")
        self._master = master_key

    def _key(self, task_id: str) -> AESGCM:
        hkdf = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"lb-vault-v1\x00" + task_id.encode()
        )
        return AESGCM(hkdf.derive(self._master))

    def seal(self, task_id: str, token: str, entry: VaultEntry) -> bytes:
        nonce = os.urandom(12)
        aad = f"{task_id}\x00{token}".encode()
        return nonce + self._key(task_id).encrypt(nonce, entry.model_dump_json().encode(), aad)

    def open(self, task_id: str, token: str, blob: bytes) -> VaultEntry:
        aad = f"{task_id}\x00{token}".encode()
        try:
            plaintext = self._key(task_id).decrypt(blob[:12], blob[12:], aad)
        except InvalidTag as e:
            raise VaultUnavailable(f"vault entry for {token} failed authentication") from e
        return VaultEntry.model_validate(json.loads(plaintext))


class Vault(Protocol):
    async def put(self, task_id: str, token: str, entry: VaultEntry) -> None: ...

    async def get(self, task_id: str, token: str) -> VaultEntry | None: ...

    async def entries(self, task_id: str) -> dict[str, VaultEntry]: ...

    async def drop(self, task_id: str) -> None: ...

    async def ping(self) -> None: ...


class RedisVault:
    def __init__(self, client: aioredis.Redis, sealer: Sealer, ttl_seconds: int) -> None:
        self._redis = client
        self._sealer = sealer
        self._ttl = ttl_seconds

    @staticmethod
    def _key(task_id: str) -> str:
        return f"vault:{task_id}"

    async def put(self, task_id: str, token: str, entry: VaultEntry) -> None:
        key = self._key(task_id)
        try:
            created = await self._redis.hsetnx(key, token, self._sealer.seal(task_id, token, entry))
            await self._redis.expire(key, self._ttl)
            if not created:
                existing = await self.get(task_id, token)
                if existing is not None and existing != entry:
                    raise TokenCollision(f"{token} already maps to a different value in task {task_id}")
        except RedisError as e:
            raise VaultUnavailable(str(e)) from e

    async def get(self, task_id: str, token: str) -> VaultEntry | None:
        try:
            blob = cast(bytes | None, await self._redis.hget(self._key(task_id), token))
        except RedisError as e:
            raise VaultUnavailable(str(e)) from e
        return None if blob is None else self._sealer.open(task_id, token, blob)

    async def entries(self, task_id: str) -> dict[str, VaultEntry]:
        try:
            raw = cast(dict[bytes, bytes], await self._redis.hgetall(self._key(task_id)))
        except RedisError as e:
            raise VaultUnavailable(str(e)) from e
        return {
            token.decode(): self._sealer.open(task_id, token.decode(), blob) for token, blob in raw.items()
        }

    async def drop(self, task_id: str) -> None:
        try:
            await self._redis.delete(self._key(task_id))
        except RedisError as e:
            raise VaultUnavailable(str(e)) from e

    async def ping(self) -> None:
        try:
            await self._redis.ping()
        except RedisError as e:
            raise VaultUnavailable(str(e)) from e


class MemoryVault:
    """Same sealing as RedisVault, kept in a dict. For tests and the offline demo."""

    def __init__(self, sealer: Sealer) -> None:
        self._sealer = sealer
        self._data: dict[str, dict[str, bytes]] = {}
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise VaultUnavailable("memory vault switched off")

    async def put(self, task_id: str, token: str, entry: VaultEntry) -> None:
        self._check()
        bucket = self._data.setdefault(task_id, {})
        if token in bucket:
            if self._sealer.open(task_id, token, bucket[token]) != entry:
                raise TokenCollision(f"{token} already maps to a different value in task {task_id}")
            return
        bucket[token] = self._sealer.seal(task_id, token, entry)

    async def get(self, task_id: str, token: str) -> VaultEntry | None:
        self._check()
        blob = self._data.get(task_id, {}).get(token)
        return None if blob is None else self._sealer.open(task_id, token, blob)

    async def entries(self, task_id: str) -> dict[str, VaultEntry]:
        self._check()
        return {t: self._sealer.open(task_id, t, b) for t, b in self._data.get(task_id, {}).items()}

    async def drop(self, task_id: str) -> None:
        self._check()
        self._data.pop(task_id, None)

    async def ping(self) -> None:
        self._check()

    def raw(self, task_id: str) -> dict[str, bytes]:
        return dict(self._data.get(task_id, {}))
