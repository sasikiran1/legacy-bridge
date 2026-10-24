"""Compiled replay scripts (ADR 0005): a successful browser run, turned into steps that run with no model.

A script is compiled offline from a task's cassettes. Every browser action the model took was chosen from the
page snapshot in the same request, so each element ref maps to its role and accessible name there. Every value
it typed was a token; tokens map back to the task's parameters, because the same keyed HMAC that minted them
can mint each parameter's token. A typed value that maps to no parameter is a compile error, so a committed
script never carries a value from the source run.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from agent.executors import element_index
from proxy.tokens import TOKEN_RE, TokenMinter

REPLAY_DIR = Path(__file__).resolve().parent / "replays"
ACTIONS = {
    "browser_navigate": "navigate",
    "browser_click": "click",
    "browser_type": "type",
    "browser_select_option": "select",
    "browser_press_key": "press",
}
_PAGE = re.compile(r'<untrusted_content source="portal-page">\n(?P<page>.*?)\n</untrusted_content>', re.S)
_PARAM = re.compile(r"\$\{([a-z_]+)\}")
MIN_ANCHOR_LITERAL = 8


class CompileError(Exception):
    pass


class Locator(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    name: str
    within_row: str | None = None
    """A parameter whose value identifies the table row holding the element, when the page repeats it."""


class Step(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["navigate", "click", "type", "select", "press"]
    url: str | None = None
    target: Locator | None = None
    value: str | None = None
    submit: bool | None = None


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str
    cassettes: str
    compiled: str


class ReplayScript(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    intent: str
    params: list[str]
    steps: list[Step]
    provenance: Provenance

    @property
    def key(self) -> str:
        return script_key(self.intent, self.params)

    def fill(self, template: str, params: dict[str, str]) -> str:
        return _PARAM.sub(lambda m: params[m.group(1)], template)

    def dump(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json", exclude_none=True), sort_keys=False, width=110)


def script_key(intent: str, params: Iterable[str]) -> str:
    """One script per intent and parameter set: changing an email and changing a phone are different flows."""
    return f"{intent}--{'-'.join(sorted(params))}"


def load_script(path: Path) -> ReplayScript:
    return ReplayScript.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _fits(masked: str, value: str) -> bool:
    """Whether a masked text could be `value` with some parts replaced by tokens."""
    pattern = "".join(".+?" if TOKEN_RE.fullmatch(part) else re.escape(part) for part in _split(masked))
    return re.fullmatch(pattern, value, re.S) is not None


def _identifies(cell: str, param: str, value: str, tokens: dict[str, str]) -> bool:
    """Whether a row cell shows this parameter: its own token, or enough literal text to match its value.
    A cell that is only some other token (a masked name) would otherwise "fit" any value."""
    if tokens.get(cell) == param:
        return True
    literal = TOKEN_RE.sub("", cell).strip()
    return len(literal) >= MIN_ANCHOR_LITERAL and _fits(cell, value)


def _split(text: str) -> list[str]:
    parts, cursor = [], 0
    for match in TOKEN_RE.finditer(text):
        parts += [text[cursor : match.start()], match.group()]
        cursor = match.end()
    return [p for p in [*parts, text[cursor:]] if p]


class Compiler:
    def __init__(self, minter: TokenMinter, entity_types: list[str]) -> None:
        self._minter = minter
        self._types = entity_types

    def compile(
        self, cassette_dir: Path, task_key: str, intent: str, params: dict[str, str], member_number: str
    ) -> ReplayScript:
        values = {**params, "member_number": member_number}
        tokens = {
            self._minter.mint(task_key, entity_type, value).render(): name
            for name, value in values.items()
            for entity_type in self._types
        }
        steps: list[Step] = []
        for call, page in _executed_browser_calls(cassette_dir):
            step = self._step(call, page, tokens, values)
            if (
                steps
                and step.action in ("type", "select")
                and (steps[-1].action, steps[-1].target)
                == (
                    step.action,
                    step.target,
                )
            ):
                steps[-1] = step  # a retyped field keeps only its last value
                continue
            steps.append(step)
        if not steps:
            raise CompileError("no executed browser actions to compile")
        return ReplayScript(
            intent=intent,
            params=sorted(params),
            steps=steps,
            provenance=Provenance(
                task=task_key,
                cassettes=cassette_dir.name,
                compiled=dt.datetime.now(dt.UTC).date().isoformat(),
            ),
        )

    def _template(self, text: str, tokens: dict[str, str], values: dict[str, str]) -> str:
        whole = tokens.get(text) or next((name for name, value in values.items() if value == text), None)
        if whole:
            return f"${{{whole}}}"
        for name, value in values.items():
            if TOKEN_RE.search(text) and _fits(text, value):
                return f"${{{name}}}"
        templated = TOKEN_RE.sub(
            lambda m: f"${{{tokens[m.group()]}}}" if m.group() in tokens else m.group(), text
        )
        if TOKEN_RE.search(templated) or any(ch.isdigit() for ch in TOKEN_RE.sub("", templated)):
            raise CompileError(f"value maps to no parameter: {text!r}")
        return templated

    def _step(
        self, call: dict[str, object], page: str, tokens: dict[str, str], values: dict[str, str]
    ) -> Step:
        action = ACTIONS[str(call["name"])]
        arguments = dict(call["arguments"])  # type: ignore[call-overload]
        if action == "navigate":
            url = re.sub(r"^https?://[^/]+", "", str(arguments["url"]))
            return Step(action=action, url=self._template(url, tokens, values))
        if action == "press":
            return Step(action=action, value=str(arguments["key"]))
        target = self._locator(page, str(arguments.get("target", "")), tokens, values)
        if action == "click":
            return Step(action=action, target=target)
        raw = arguments.get("text") if action == "type" else (arguments.get("values") or [""])[0]
        return Step(
            action=action,
            target=target,
            value=self._template(str(raw), tokens, values),
            submit=True if arguments.get("submit") else None,
        )

    def _locator(self, page: str, ref: str, tokens: dict[str, str], values: dict[str, str]) -> Locator:
        elements = element_index(page)
        if ref not in elements:
            raise CompileError(f"ref {ref} is not in the snapshot it was chosen from")
        role, raw_name = elements[ref]
        if not raw_name:
            raise CompileError(f"{role} {ref} has no accessible name to locate it by")
        name = self._template(raw_name, tokens, values)
        # An element in a table row is anchored to the row whenever a parameter identifies it, even if it is
        # unique on the source page: another member's page may list several rows with the same button.
        row = _row_text(page, ref)
        for param, value in values.items():
            if any(_identifies(cell, param, value, tokens) for cell in row):
                return Locator(role=role, name=name, within_row=param)
        if sum(1 for r, n in elements.values() if (r, n) == (role, raw_name)) == 1:
            return Locator(role=role, name=name)
        raise CompileError(f"{role} {raw_name!r} is repeated on the page and no parameter identifies its row")


def _row_text(page: str, ref: str) -> list[str]:
    """Quoted cell texts of the table row holding `ref`, from the indented snapshot tree."""
    lines = page.splitlines()
    index = next((i for i, line in enumerate(lines) if f"[ref={ref}]" in line), None)
    if index is None:
        return []
    depth = len(lines[index]) - len(lines[index].lstrip())
    for i in range(index - 1, -1, -1):
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent < depth and lines[i].lstrip().startswith("- row"):
            cells = []
            for line in lines[i + 1 :]:
                if len(line) - len(line.lstrip()) <= indent:
                    break
                cells += re.findall(r'cell "((?:[^"\\]|\\.)*)"', line)
            return cells
        depth = min(depth, indent)
    return []


def _executed_browser_calls(cassette_dir: Path) -> list[tuple[dict[str, object], str]]:
    """(tool call, the page it was chosen from) for every browser action whose result was a page."""
    exchanges = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(cassette_dir.glob("[0-9][0-9][0-9].json"))
    ]
    results: dict[str, str] = {}
    for exchange in exchanges:
        for message in exchange["request"]["messages"]:
            if message.get("role") == "tool" and message.get("content") != "[earlier observation omitted]":
                results.setdefault(message["tool_call_id"], message.get("content") or "")
    calls = []
    for exchange in exchanges:
        page = _latest_page(exchange["request"]["messages"])
        reply = json.loads(exchange["body"])["choices"][0]["message"]
        for call in reply.get("tool_calls") or []:
            name = call["function"]["name"]
            if name not in ACTIONS:
                continue
            outcome = results.get(call["id"], "")
            if not outcome.startswith('<untrusted_content source="portal-page">'):
                continue
            calls.append(({"name": name, "arguments": json.loads(call["function"]["arguments"])}, page))
    return calls


def _latest_page(messages: list[dict[str, object]]) -> str:
    for message in reversed(messages):
        match = _PAGE.search(str(message.get("content") or ""))
        if match:
            return match.group("page")
    return ""
