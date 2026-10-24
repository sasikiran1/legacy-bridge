"""The agent's state machine (LangGraph).

    plan -> api_step <-> api_act -> verify
                 \\           \\
                  `-> replay --> verify          (a compiled script, no model; ADR 0005)
                        |   \\
                        |    `-> to_explore -> browser_start -> explore_step <-> explore_act -> verify
                        |                            |                 |              |
                        `-------------------------> human <------------+--------------'

State is plain, JSON-serialisable and tokenized: executors live in `Runtime`, outside the checkpoint, and mask
every result before it is stored. Interrupts (approvals, verification walls, exhausted budgets) pause the
graph until a person, or the experiment runner's approval policy, resumes it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from agent.executors import FALLBACK, ApiResult, BrowserObservation, trim_snapshot
from agent.guardrails import (
    CONFIRMATION,
    InjectionScanner,
    approval_for_click,
    approval_for_operation,
    is_verification_wall,
    spotlight,
)
from agent.llm import LlmReply, ToolCall
from agent.openapi import Operation
from agent.prompts import AGENT_TOOLS, API, BROWSER_TOOL_SPECS, EXPLORE, PLAN
from agent.settings import AgentSettings
from agent.telemetry import tracer

_KEEP_OBSERVATIONS = 2


class AgentState(TypedDict, total=False):
    task_key: str
    instruction: str
    username: str
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cassette_mismatches: int
    models: list[str]
    plan: dict[str, Any]
    tier: str
    tiers: list[str]
    messages: list[dict[str, Any]]
    pending: list[dict[str, Any]]
    nudges: int
    api_steps: int
    explore_steps: int
    api_failures: list[dict[str, Any]]
    fallback_reason: str | None
    handoff_reason: str | None
    last_observation: str
    quarantined: bool
    approvals: list[dict[str, Any]]
    writes: list[dict[str, Any]]
    answer: str | None
    summary: str | None
    finished: bool
    verified: bool | None
    outcome: str | None
    replay_outcome: str | None
    replay_failure: str | None


class ReplayRun(Protocol):
    """The result of running a compiled script (agent.replay_runner.ReplayResult)."""

    @property
    def outcome(self) -> str: ...
    @property
    def failed_step(self) -> int | None: ...
    @property
    def error(self) -> str | None: ...
    @property
    def approvals(self) -> list[dict[str, object]]: ...


class Browser(Protocol):
    async def login(self, username: str, password: str) -> str: ...

    def describe(self, ref: str) -> tuple[str, str] | None: ...

    async def act(self, task_id: str, tool: str, arguments: dict[str, Any]) -> BrowserObservation: ...

    async def close(self) -> None: ...


class Api(Protocol):
    operations: dict[str, Operation]

    def available(self) -> list[Operation]: ...

    async def call(
        self, task_id: str, credentials: tuple[str, str], operation_id: str, arguments: dict[str, Any]
    ) -> ApiResult: ...


class Masking(Protocol):
    async def text(self, task_id: str, content: str) -> str: ...


class Chat(Protocol):
    async def chat(
        self,
        task_id: str,
        call: int,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> LlmReply: ...


@dataclass
class Runtime:
    """Everything the graph acts through. Not part of the checkpointed state."""

    settings: AgentSettings
    llm: Chat
    api: Api
    masker: Masking
    scanner: InjectionScanner
    password: str
    open_browser: Callable[[], Awaitable[Browser]]
    browser: Browser | None = None
    replay: Callable[[], Awaitable[ReplayRun]] | None = None
    """Runs this task's compiled script, if there is one. It holds the task's real parameter values, which is
    why it lives here and not in the checkpointed state (ADR 0012)."""
    replay_hint: str = ""
    """The script's steps as templates, no values: what the browser tier is told when a step breaks."""

    async def ensure_browser(self) -> Browser:
        if self.browser is None:
            self.browser = await self.open_browser()
        return self.browser

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
            self.browser = None


def _usage(state: AgentState, reply: LlmReply) -> dict[str, Any]:
    return {
        "llm_calls": state.get("llm_calls", 0) + 1,
        "tokens_in": state.get("tokens_in", 0) + reply.input_tokens,
        "tokens_out": state.get("tokens_out", 0) + reply.output_tokens,
        "cassette_mismatches": state.get("cassette_mismatches", 0) + (reply.cassette == "mismatch"),
        "models": sorted({*state.get("models", []), reply.model}),
    }


def _pending(reply: LlmReply) -> list[dict[str, Any]]:
    """Tool calls to run. A reply with none gets a nudge, never an implicit finish: free text such as
    "cannot_use_api because ..." must not count as a completed task."""
    return [c.model_dump() for c in reply.tool_calls]


_TEXT_CALL = re.compile(
    r"\s*(?:<tool_call>)?\s*([A-Za-z_]\w*)\s*[:(]?\s*(\{.*\})\s*\)?\s*(?:</tool_call>)?\s*", re.S
)


def _recover_text_call(reply: LlmReply, tools: list[dict[str, Any]]) -> LlmReply:
    """Local models sometimes write a call as text (`finish: {...}`). The whole reply must be one offered tool
    name and a JSON object; anything else stays free text and gets a nudge."""
    if reply.tool_calls or not reply.content:
        return reply
    match = _TEXT_CALL.fullmatch(reply.content)
    if not match or match.group(1) not in {t["function"]["name"] for t in tools}:
        return reply
    try:
        arguments = json.loads(match.group(2))
    except ValueError:
        return reply
    if not isinstance(arguments, dict):
        return reply
    call = ToolCall(id="text-call", name=match.group(1), arguments=arguments, raw_arguments=match.group(2))
    return reply.model_copy(update={"content": "", "tool_calls": (call,)})


NUDGE = "Respond by calling one of the available tools. Use finish when the task is done."


MAX_NUDGES = 3
PAGE_CHANGING = frozenset({"browser_navigate", "browser_navigate_back", "browser_click", "browser_press_key"})


def _nudge(state: AgentState, give_up: dict[str, Any]) -> dict[str, Any]:
    """Answers a tool-less reply with a reminder; after MAX_NUDGES in a row the tier gives up."""
    nudges = state.get("nudges", 0) + 1
    update = {
        "messages": [*state.get("messages", []), {"role": "user", "content": NUDGE}],
        "nudges": nudges,
        "pending": [],
    }
    return {**update, **give_up} if nudges >= MAX_NUDGES else update


def _plan_summary(state: AgentState) -> dict[str, Any]:
    """Intent and kind only: planner parameters can contain invented placeholders, the instruction cannot."""
    plan = state.get("plan", {})
    return {"intent": plan.get("intent"), "kind": plan.get("kind")}


def _compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keeps the last few page observations verbatim and stubs older ones, to bound context growth."""
    observation_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    stale = set(observation_indexes[:-_KEEP_OBSERVATIONS])
    return [
        {**m, "content": "[earlier observation omitted]"} if i in stale else m for i, m in enumerate(messages)
    ]


def _tool_message(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _finish(state: AgentState, call: ToolCall, calls: list[ToolCall]) -> tuple[dict[str, Any] | None, str]:
    """Accepts finish only on its own (the model has not yet seen the results of calls made alongside it) and,
    for a question, only with an answer. Returns the state update, or None with the reason it was refused."""
    if len(calls) > 1:
        return (
            None,
            "Not recorded: call finish on its own, after you have seen the results of your other calls.",
        )
    answer = call.arguments.get("answer")
    if state.get("plan", {}).get("kind") == "read" and not answer:
        return (
            None,
            "Not recorded: this task asks a question. Call finish with the requested value in `answer`.",
        )
    return {"answer": answer, "summary": call.arguments.get("summary"), "finished": True}, "Recorded."


def _parse_plan(text: str | None) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group())
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_graph(rt: Runtime, checkpointer: BaseCheckpointSaver[Any] | None = None) -> CompiledStateGraph[Any]:
    settings = rt.settings

    async def chat(
        state: AgentState,
        node: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int = 1024,
    ) -> LlmReply:
        with tracer().start_as_current_span(f"node {node}"):
            return await rt.llm.chat(
                state["task_key"], state.get("llm_calls", 0), settings.role(node), messages, tools, max_tokens
            )

    async def plan(state: AgentState) -> dict[str, Any]:
        messages = [{"role": "system", "content": PLAN}, {"role": "user", "content": state["instruction"]}]
        reply = await chat(state, "plan", messages, None, max_tokens=300)
        return {
            **_usage(state, reply),
            "plan": _parse_plan(reply.content),
            "tier": "api",
            "tiers": ["api"],
            "messages": [],
        }

    async def api_step(state: AgentState) -> dict[str, Any]:
        if state.get("api_steps", 0) >= settings.max_api_steps:
            return {"fallback_reason": "API step budget exhausted"}
        operations = rt.api.available()
        if not operations:
            return {"fallback_reason": "every API circuit is open"}
        messages = state.get("messages") or [
            {"role": "system", "content": API},
            {
                "role": "user",
                "content": f"Task: {state['instruction']}\nPlan: {json.dumps(state.get('plan', {}))}",
            },
        ]
        tools = [op.tool() for op in operations] + [AGENT_TOOLS["finish"], AGENT_TOOLS["cannot_use_api"]]
        reply = _recover_text_call(await chat(state, "api", messages, tools), tools)
        return {
            **_usage(state, reply),
            "messages": [*messages, reply.assistant_message()],
            "pending": _pending(reply),
            "api_steps": state.get("api_steps", 0) + 1,
        }

    async def api_act(state: AgentState) -> dict[str, Any]:
        calls = [ToolCall.model_validate(c) for c in state.get("pending", [])]
        if not calls:
            return _nudge(state, {"fallback_reason": "the model stopped calling tools"})
        quarantined = state.get("quarantined", False)
        needs = {c.id: kind.value for c in calls if (kind := approval_for_operation(c.name, quarantined))}
        decision: dict[str, Any] = {}
        if needs:
            decision = interrupt(
                {
                    "kind": "approval",
                    "actions": [
                        {"tool": c.name, "arguments": c.arguments, "approval": needs[c.id]}
                        for c in calls
                        if c.id in needs
                    ],
                }
            )
        approvals = list(state.get("approvals", []))
        messages = list(state.get("messages", []))
        writes = list(state.get("writes", []))
        failures = list(state.get("api_failures", []))
        update: dict[str, Any] = {}
        stop = False
        for call in calls:
            if stop:
                messages.append(_tool_message(call.id, "Not executed."))
                continue
            if call.name == "finish":
                finished, reply = _finish(state, call, calls)
                messages.append(_tool_message(call.id, reply))
                if finished:
                    update |= finished
                    stop = True
                continue
            if call.name == "cannot_use_api":
                update["fallback_reason"] = f"model: {call.arguments.get('reason', '')}"
                messages.append(_tool_message(call.id, "Handing over to the browser."))
                stop = True
                continue
            if call.parse_error:
                messages.append(_tool_message(call.id, f"Invalid call: {call.parse_error}"))
                continue
            if call.id in needs:
                approved = bool(decision.get("approved"))
                approvals.append({"tool": call.name, "kind": needs[call.id], "approved": approved})
                if not approved:
                    messages.append(
                        _tool_message(call.id, "A reviewer declined this action. Do not retry it.")
                    )
                    continue
            result = await rt.api.call(
                state["task_key"], (state["username"], rt.password), call.name, call.arguments
            )
            text = result.for_model()
            if rt.scanner.scan(text).flagged:
                quarantined = True
                text = "[flagged as possible prompt injection]\n" + text
            messages.append(_tool_message(call.id, spotlight(text, "portal-api")))
            operation = rt.api.operations.get(call.name)
            if result.ok and operation is not None and operation.writes:
                writes.append({"tier": "api", "action": call.name, "ok": True})
            if result.failure in FALLBACK:
                failures.append(
                    {"operation": call.name, "failure": str(result.failure), "status": result.status}
                )
                update["fallback_reason"] = f"{call.name}: {result.failure}"
                stop = True
        return {
            **update,
            "messages": messages,
            "pending": [],
            "nudges": 0,
            "approvals": approvals,
            "writes": writes,
            "api_failures": failures,
            "quarantined": quarantined,
        }

    async def replay(state: AgentState) -> dict[str, Any]:
        """Runs the compiled script with no model; a broken step falls through to the browser tier."""
        if rt.replay is None:
            return {}
        with tracer().start_as_current_span("node replay"):
            result = await rt.replay()
        update: dict[str, Any] = {
            "tier": "replay",
            "tiers": [*state.get("tiers", []), "replay"],
            "replay_outcome": result.outcome,
            "approvals": [*state.get("approvals", []), *result.approvals],
        }
        if result.outcome == "success":
            writes = [*state.get("writes", []), {"tier": "replay", "action": "script", "ok": True}]
            return {
                **update,
                "writes": writes,
                "finished": True,
                "summary": "Completed by the replay script.",
            }
        if result.outcome == "declined":
            return {**update, "finished": True, "summary": "A reviewer declined the script's action."}
        if result.outcome == "handoff":
            return {**update, "handoff_reason": "verification wall during replay"}
        step = "" if result.failed_step is None else f"step {result.failed_step + 1} "
        return {**update, "replay_failure": f"{step}{result.outcome}: {result.error or 'no confirmation'}"}

    async def to_explore(state: AgentState) -> dict[str, Any]:
        return {
            "tier": "explore",
            "tiers": [*state.get("tiers", []), "explore"],
            "messages": [],
            "pending": [],
        }

    async def browser_start(state: AgentState) -> dict[str, Any]:
        browser = await rt.ensure_browser()
        raw = await browser.login(state["username"], rt.password)
        if is_verification_wall(raw):
            return {"handoff_reason": "verification wall at sign-in"}
        shown = trim_snapshot(raw) if settings.snapshots == "trimmed" else raw
        return {"last_observation": await rt.masker.text(state["task_key"], shown), "handoff_reason": None}

    async def explore_step(state: AgentState) -> dict[str, Any]:
        if state.get("explore_steps", 0) >= settings.max_explore_steps:
            return {"handoff_reason": "browser step budget exhausted"}
        heal = ""
        if state.get("replay_failure") and rt.replay_hint:
            heal = (
                f"\n\nA recorded script for this kind of task stopped at {state['replay_failure']}. "
                f"Its steps were: {rt.replay_hint}. The page may have changed; do the task in the browser.\n"
            )
        messages = state.get("messages") or [
            {"role": "system", "content": EXPLORE.format(origin=settings.portal_origin)},
            {
                "role": "user",
                "content": f"Task: {state['instruction']}{heal}\n\nCurrent page:\n"
                + spotlight(state.get("last_observation", ""), "portal-page"),
            },
        ]
        tools = [*BROWSER_TOOL_SPECS, AGENT_TOOLS["finish"], AGENT_TOOLS["request_human"]]
        reply = _recover_text_call(await chat(state, "explore", _compact(messages), tools), tools)
        return {
            **_usage(state, reply),
            "messages": [*messages, reply.assistant_message()],
            "pending": _pending(reply),
            "explore_steps": state.get("explore_steps", 0) + 1,
        }

    async def explore_act(state: AgentState) -> dict[str, Any]:
        browser = await rt.ensure_browser()
        calls = [ToolCall.model_validate(c) for c in state.get("pending", [])]
        if not calls:
            return _nudge(state, {"handoff_reason": "the model stopped calling tools"})
        quarantined = state.get("quarantined", False)
        needs = {}
        for c in calls:
            if c.name == "browser_click":
                element = browser.describe(str(c.arguments.get("target", "")))
                if element and (kind := approval_for_click(*element, quarantined)):
                    needs[c.id] = (kind.value, element[1])
        decision: dict[str, Any] = {}
        if needs:
            decision = interrupt(
                {
                    "kind": "approval",
                    "actions": [
                        {"tool": c.name, "element": needs[c.id][1], "approval": needs[c.id][0]}
                        for c in calls
                        if c.id in needs
                    ],
                }
            )
        approvals = list(state.get("approvals", []))
        messages = list(state.get("messages", []))
        writes = list(state.get("writes", []))
        observation = state.get("last_observation", "")
        update: dict[str, Any] = {}
        stop = False
        page_changed = False
        for call in calls:
            if stop:
                messages.append(_tool_message(call.id, "Not executed."))
                continue
            if page_changed and "target" in call.arguments:
                # Refs are renumbered on every snapshot; ones chosen before this turn's page change are stale.
                messages.append(
                    _tool_message(
                        call.id, "Not executed: the page changed. Use refs from the latest snapshot."
                    )
                )
                continue
            if call.name == "finish":
                finished, reply = _finish(state, call, calls)
                messages.append(_tool_message(call.id, reply))
                if finished:
                    update |= finished
                    stop = True
                continue
            if call.name == "request_human":
                update["handoff_reason"] = f"model: {call.arguments.get('reason', '')}"
                messages.append(_tool_message(call.id, "Handed to a person."))
                stop = True
                continue
            if call.parse_error:
                messages.append(_tool_message(call.id, f"Invalid call: {call.parse_error}"))
                continue
            if call.id in needs:
                approved = bool(decision.get("approved"))
                approvals.append({"tool": call.name, "kind": needs[call.id][0], "approved": approved})
                if not approved:
                    messages.append(
                        _tool_message(call.id, "A reviewer declined this action. Do not retry it.")
                    )
                    continue
            clicked = (
                browser.describe(str(call.arguments.get("target", "")))
                if call.name == "browser_click"
                else None
            )
            result = await browser.act(state["task_key"], call.name, call.arguments)
            page_changed = page_changed or call.name in PAGE_CHANGING or bool(call.arguments.get("submit"))
            if result.error:
                messages.append(_tool_message(call.id, spotlight(f"Error: {result.error}", "portal-browser")))
                continue
            if is_verification_wall(result.raw):
                update["handoff_reason"] = "verification wall"
                stop = True
            text = result.text
            if rt.scanner.scan(text).flagged:
                quarantined = True
                text = "[flagged as possible prompt injection]\n" + text
            observation = text
            messages.append(_tool_message(call.id, spotlight(text, "portal-page")))
            if clicked and re.search(
                r"\b(save|update|add|submit|confirm|request|appeal|delete|remove)\b", clicked[1], re.I
            ):
                writes.append({"tier": "browser", "action": f"click {clicked[1]}", "ok": True})
        return {
            **update,
            "messages": messages,
            "pending": [],
            "nudges": 0,
            "approvals": approvals,
            "writes": writes,
            "last_observation": observation,
            "quarantined": quarantined,
        }

    async def human(state: AgentState) -> dict[str, Any]:
        resolution = interrupt({"kind": "handoff", "reason": state.get("handoff_reason")})
        if isinstance(resolution, dict) and resolution.get("resolved"):
            return {"handoff_reason": None, "messages": [], "pending": []}
        return {"outcome": "handoff"}

    async def verify(state: AgentState) -> dict[str, Any]:
        writes = state.get("writes", [])
        if not writes:
            # A change task that finished without executing any write did not do its job, whatever it says;
            # a summary alone never verifies anything.
            verified = state.get("plan", {}).get("kind") != "write" and bool(state.get("answer"))
        elif any(w["tier"] == "browser" for w in writes):
            verified = bool(CONFIRMATION.search(state.get("last_observation", "")))
        else:
            verified = all(w["ok"] for w in writes)
        return {"verified": verified, "outcome": "success" if verified else "unverified"}

    def after_api_step(state: AgentState) -> str:
        return "replay" if state.get("fallback_reason") else "api_act"

    def after_api_act(state: AgentState) -> str:
        if state.get("finished"):
            return "verify"
        return "replay" if state.get("fallback_reason") else "api_step"

    def after_replay(state: AgentState) -> str:
        if state.get("finished"):
            return "verify"
        return "human" if state.get("handoff_reason") else "to_explore"

    def after_browser_start(state: AgentState) -> str:
        return "human" if state.get("handoff_reason") else "explore_step"

    def after_explore_step(state: AgentState) -> str:
        return "human" if state.get("handoff_reason") else "explore_act"

    def after_explore_act(state: AgentState) -> str:
        if state.get("finished"):
            return "verify"
        return "human" if state.get("handoff_reason") else "explore_step"

    def after_human(state: AgentState) -> str:
        return END if state.get("outcome") == "handoff" else "browser_start"

    graph = StateGraph(AgentState)
    for name, node in [
        ("plan", plan),
        ("api_step", api_step),
        ("api_act", api_act),
        ("replay", replay),
        ("to_explore", to_explore),
        ("browser_start", browser_start),
        ("explore_step", explore_step),
        ("explore_act", explore_act),
        ("human", human),
        ("verify", verify),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "api_step")
    graph.add_conditional_edges("api_step", after_api_step, ["replay", "api_act"])
    graph.add_conditional_edges("api_act", after_api_act, ["verify", "replay", "api_step"])
    graph.add_conditional_edges("replay", after_replay, ["verify", "human", "to_explore"])
    graph.add_edge("to_explore", "browser_start")
    graph.add_conditional_edges("browser_start", after_browser_start, ["human", "explore_step"])
    graph.add_conditional_edges("explore_step", after_explore_step, ["human", "explore_act"])
    graph.add_conditional_edges("explore_act", after_explore_act, ["verify", "human", "explore_step"])
    graph.add_conditional_edges("human", after_human, [END, "browser_start"])
    graph.add_edge("verify", END)
    return graph.compile(checkpointer=checkpointer)
