"""Rehydration (ADR 0003): final text on request, SSE streams with a lookahead buffer, and tool arguments
at the execution boundary under the rehydration policy."""

from __future__ import annotations

import re
from collections.abc import Mapping

from proxy.audit import Action, AuditLog
from proxy.policy import PiiPolicy
from proxy.tokens import MAX_TOKEN_LENGTH, OPEN, TOKEN_RE
from proxy.vault import Vault

_VIABLE_PREFIX = re.compile(r"\[\[(?:[A-Z][A-Z_]*(?::[0-9a-f]{0,10}(?:\])?)?)?")


class RehydrationDenied(Exception):
    """The executor refused to restore a value into a tool argument."""


class Rehydrator:
    def __init__(self, vault: Vault, audit: AuditLog) -> None:
        self._vault = vault
        self._audit = audit

    async def token(self, token: str, task_id: str, destination: str) -> str | None:
        match = TOKEN_RE.fullmatch(token)
        entry = await self._vault.get(task_id, token) if match else None
        entity_type = match.group(1) if match else "UNKNOWN"
        if entry is None or entry.entity_type != entity_type:
            await self._audit.record(task_id, Action.UNRESOLVED, entity_type, token, destination)
            return None
        await self._audit.record(task_id, Action.UNMASK, entity_type, token, destination)
        return entry.value

    async def text(self, text: str, task_id: str, destination: str) -> str:
        pieces, cursor = [], 0
        for match in TOKEN_RE.finditer(text):
            pieces.append(text[cursor : match.start()])
            value = await self.token(match.group(), task_id, destination)
            pieces.append(match.group() if value is None else value)
            cursor = match.end()
        pieces.append(text[cursor:])
        return "".join(pieces)


class StreamRehydrator:
    """Rehydrates text that arrives in arbitrary chunks. Holds back output from a `[[` until the token closes,
    or until the held text can no longer become a token, so a split token is never emitted raw."""

    def __init__(self, rehydrator: Rehydrator, task_id: str, destination: str) -> None:
        self._rehydrator = rehydrator
        self._task_id = task_id
        self._destination = destination
        self._buffer = ""

    @property
    def pending(self) -> str:
        return self._buffer

    async def feed(self, chunk: str) -> str:
        self._buffer += chunk
        out: list[str] = []
        while True:
            opener = self._buffer.find(OPEN)
            if opener == -1:
                # A trailing "[" may be the first half of the next chunk's opener.
                keep = 1 if self._buffer.endswith(OPEN[0]) else 0
                out.append(self._buffer[: len(self._buffer) - keep])
                self._buffer = self._buffer[len(self._buffer) - keep :]
                break
            out.append(self._buffer[:opener])
            rest = self._buffer[opener:]
            token = TOKEN_RE.match(rest)
            if token:
                value = await self._rehydrator.token(token.group(), self._task_id, self._destination)
                out.append(token.group() if value is None else value)
                self._buffer = rest[token.end() :]
                continue
            prefix = _VIABLE_PREFIX.match(rest)
            still_possible = prefix is not None and prefix.end() == len(rest) and len(rest) < MAX_TOKEN_LENGTH
            if still_possible:
                self._buffer = rest
                break
            # Not a token: release one character only, since a real opener may start right after it.
            out.append(rest[0])
            self._buffer = rest[1:]
        return "".join(out)

    async def finish(self) -> str:
        rest, self._buffer = self._buffer, ""
        return rest


class ToolArgumentResolver:
    """Restores real values into tool arguments immediately before execution, if the policy allows it."""

    def __init__(
        self, vault: Vault, audit: AuditLog, policy: PiiPolicy, allowed_hosts: frozenset[str]
    ) -> None:
        self._vault = vault
        self._audit = audit
        self._policy = policy
        self._allowed_hosts = allowed_hosts

    async def resolve(
        self, task_id: str, tool: str, arguments: Mapping[str, object], target_host: str | None = None
    ) -> dict[str, object]:
        if target_host is not None and target_host not in self._allowed_hosts:
            raise RehydrationDenied(f"{target_host} is not on the domain allowlist")
        return {name: await self._value(task_id, tool, name, value) for name, value in arguments.items()}

    async def _value(self, task_id: str, tool: str, argument: str, value: object) -> object:
        match value:
            case str():
                return await self._string(task_id, tool, argument, value)
            case list():
                return [await self._value(task_id, tool, argument, item) for item in value]
            case dict():
                return {k: await self._value(task_id, tool, argument, v) for k, v in value.items()}
        return value

    async def resolve_field(self, task_id: str, tool: str, label: str | None, text: str) -> str:
        """Restores tokens typed into a UI field; the policy decides by the field's accessible name."""
        return await self._string(task_id, tool, f"field:{label or ''}", text, label=label, by_label=True)

    async def _string(
        self,
        task_id: str,
        tool: str,
        argument: str,
        text: str,
        label: str | None = None,
        by_label: bool = False,
    ) -> str:
        destination = f"tool:{tool}.{argument}"
        pieces, cursor = [], 0
        for match in TOKEN_RE.finditer(text):
            token, entity_type = match.group(), match.group(1)
            entry = await self._vault.get(task_id, token)
            if entry is None or entry.entity_type != entity_type:
                await self._audit.record(task_id, Action.UNRESOLVED, entity_type, token, destination)
                raise RehydrationDenied(f"{token} is not a token of this task")
            rule = self._policy.entities.get(entity_type)
            allowed = (
                self._policy.may_type_into(entity_type, label)
                if by_label
                else self._policy.may_rehydrate(tool, argument, entity_type)
            )
            if rule is None or not rule.rehydratable or not allowed:
                await self._audit.record(task_id, Action.UNMASK_DENIED, entity_type, token, destination)
                raise RehydrationDenied(f"policy does not allow {entity_type} in {tool}.{argument}")
            await self._audit.record(task_id, Action.UNMASK, entity_type, token, destination)
            pieces.append(text[cursor : match.start()])
            pieces.append(entry.value)
            cursor = match.end()
        pieces.append(text[cursor:])
        return "".join(pieces)
