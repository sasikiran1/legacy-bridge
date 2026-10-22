"""The state machine's paths, driven by a scripted model and fake executors (no LLM, portal or browser)."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.executors import ApiResult, BrowserObservation, Failure
from agent.graph import Browser, Runtime, build_graph
from agent.guardrails import NoScanner
from agent.llm import LlmReply, ToolCall
from agent.openapi import Operation, Parameter
from agent.settings import AgentSettings, Profile

SETTINGS = AgentSettings(
    proxy_url="http://proxy",
    portal_url="http://portal:8080",
    redis_url="redis://x",
    vault_master_key=bytes(32),
    vault_task_ttl_seconds=60,
    agent_db_url="postgresql://x",
    test_user_password="pw",
    profile=Profile.LOCAL,
    injection_scanner="none",
)


def reply(*calls: tuple[str, dict[str, Any]], content: str = "") -> LlmReply:
    return LlmReply(
        content=content,
        tool_calls=tuple(
            ToolCall(id=f"c{i}", name=name, arguments=args, raw_arguments=json.dumps(args))
            for i, (name, args) in enumerate(calls)
        ),
        model="scripted",
        input_tokens=10,
        output_tokens=5,
        latency_ms=1.0,
        cassette=None,
        finish_reason="tool_calls" if calls else "stop",
    )


class ScriptedLlm:
    def __init__(self, replies: list[LlmReply]) -> None:
        self.replies = list(replies)
        self.calls: list[int] = []

    async def chat(
        self,
        task_id: str,
        call: int,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> LlmReply:
        self.calls.append(call)
        return self.replies.pop(0)


def op(op_id: str, method: str) -> Operation:
    return Operation(
        id=op_id,
        method=method,
        path=f"/api/v1/{op_id}",
        summary=op_id,
        parameters=(Parameter(name="memberNumber", location="path", required=True, schema_type="string"),),
        body_fields=(),
        response_fields=(),
        response_is_list=False,
    )


class FakeApi:
    def __init__(self, results: dict[str, ApiResult]) -> None:
        self.operations = {name: op(name, "GET" if name.startswith("get") else "POST") for name in results}
        self.results = results
        self.executed: list[str] = []

    def available(self) -> list[Operation]:
        return list(self.operations.values())

    async def call(
        self, task_id: str, credentials: tuple[str, str], operation_id: str, arguments: dict[str, Any]
    ) -> ApiResult:
        self.executed.append(operation_id)
        return self.results[operation_id]


class FakeBrowser:
    def __init__(self, login_page: str, pages: list[str]) -> None:
        self.login_page = login_page
        self.pages = list(pages)
        self.actions: list[str] = []

    async def login(self, username: str, password: str) -> str:
        return self.login_page

    def describe(self, ref: str) -> tuple[str, str] | None:
        return {"e5": ("button", "Save address"), "e6": ("button", "Yes, delete note")}.get(ref)

    async def act(self, task_id: str, tool: str, arguments: dict[str, Any]) -> BrowserObservation:
        self.actions.append(tool)
        page = self.pages.pop(0)
        return BrowserObservation(text=page, raw=page)

    async def close(self) -> None:
        pass


class Identity:
    async def text(self, task_id: str, content: str) -> str:
        return content


def ok(op_id: str, body: object = None) -> ApiResult:
    return ApiResult(operation=op_id, status=200, failure=None, body=body, detail="")


def failed(op_id: str, failure: Failure, status: int) -> ApiResult:
    return ApiResult(operation=op_id, status=status, failure=failure, body=None, detail="")


async def run(
    llm: ScriptedLlm,
    api: FakeApi,
    browser: FakeBrowser | None = None,
    resume: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    async def open_browser() -> Browser:
        assert browser is not None
        return browser

    runtime = Runtime(
        settings=SETTINGS,
        llm=llm,
        api=api,
        masker=Identity(),
        scanner=NoScanner(),
        password="pw",
        open_browser=open_browser,
    )
    graph = build_graph(runtime, InMemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "t"}}
    payload: Any = {"task_key": "healthy/t-01", "username": "clerk01", "instruction": "do it"}
    interrupts: list[dict[str, Any]] = []
    resumes = list(resume or [])
    while True:
        state = await graph.ainvoke(payload, config)
        if not state.get("__interrupt__"):
            return state, interrupts
        interrupts.append(state["__interrupt__"][0].value)
        payload = Command(resume=resumes.pop(0))


PLAN = reply(content='{"intent": "x", "kind": "read"}')
WRITE_PLAN = reply(content='{"intent": "x", "kind": "write"}')


async def test_api_tier_completes_a_read_without_the_browser() -> None:
    llm = ScriptedLlm(
        [
            PLAN,
            reply(("getClaim", {"memberNumber": "m"})),
            reply(("finish", {"answer": "PAID", "summary": "s"})),
        ]
    )
    api = FakeApi({"getClaim": ok("getClaim", {"status": "PAID"})})
    state, _ = await run(llm, api)
    assert state["outcome"] == "success"
    assert state["answer"] == "PAID"
    assert state["tiers"] == ["api"]
    assert llm.calls == [0, 1, 2]


async def test_a_removed_endpoint_falls_back_to_the_browser() -> None:
    llm = ScriptedLlm(
        [
            WRITE_PLAN,
            reply(("updateAddress", {"memberNumber": "m"})),
            reply(("browser_click", {"target": "e5"})),
            reply(("finish", {"summary": "saved"})),
        ]
    )
    api = FakeApi({"updateAddress": failed("updateAddress", Failure.NOT_FOUND, 404)})
    browser = FakeBrowser('- heading "Member Search"', ["- status [ref=e9]: Address saved."])
    state, _ = await run(llm, api, browser)
    assert state["tiers"] == ["api", "explore"]
    assert state["fallback_reason"] == "updateAddress: not_found"
    assert browser.actions == ["browser_click"]
    assert state["outcome"] == "success"
    assert state["verified"] is True


async def test_a_declined_payment_is_never_executed() -> None:
    llm = ScriptedLlm(
        [
            WRITE_PLAN,
            reply(("submitReimbursement", {"memberNumber": "m"})),
            reply(("finish", {"summary": "not paid"})),
        ]
    )
    api = FakeApi({"submitReimbursement": ok("submitReimbursement")})
    state, interrupts = await run(llm, api, resume=[{"approved": False}])
    assert interrupts[0]["kind"] == "approval"
    assert interrupts[0]["actions"][0]["approval"] == "payment"
    assert api.executed == []
    assert state["approvals"] == [{"tool": "submitReimbursement", "kind": "payment", "approved": False}]


async def test_a_verification_wall_hands_off_to_a_person() -> None:
    llm = ScriptedLlm([PLAN, reply(("cannot_use_api", {"reason": "retired"}))])
    browser = FakeBrowser('- heading "Security check" [level=1]', [])
    state, interrupts = await run(
        llm, FakeApi({"getMember": ok("getMember")}), browser, resume=[{"resolved": False}]
    )
    assert interrupts == [{"kind": "handoff", "reason": "verification wall at sign-in"}]
    assert state["outcome"] == "handoff"


async def test_free_text_without_a_tool_call_is_not_a_finish() -> None:
    llm = ScriptedLlm(
        [
            PLAN,
            reply(content="cannot_use_api because the endpoint is gone"),
            reply(("getClaim", {"memberNumber": "m"})),
            reply(("finish", {"answer": "PAID", "summary": "s"})),
        ]
    )
    api = FakeApi({"getClaim": ok("getClaim", {"status": "PAID"})})
    state, _ = await run(llm, api)
    assert state["answer"] == "PAID"
    assert any(m.get("content", "").startswith("Respond by calling") for m in state["messages"])


async def test_refs_chosen_before_a_page_change_are_not_used() -> None:
    llm = ScriptedLlm(
        [
            WRITE_PLAN,
            reply(("cannot_use_api", {"reason": "x"})),
            reply(("browser_navigate", {"url": "/members/m"}), ("browser_click", {"target": "e5"})),
            reply(("finish", {"summary": "done"})),
        ]
    )
    browser = FakeBrowser("home", ["member page"])
    state, _ = await run(llm, FakeApi({"getMember": ok("getMember")}), browser)
    assert browser.actions == ["browser_navigate"]
    assert any("page changed" in m.get("content", "") for m in state["messages"] if m["role"] == "tool")


async def test_finish_alongside_other_calls_is_not_recorded() -> None:
    llm = ScriptedLlm(
        [
            PLAN,
            reply(("getClaim", {"memberNumber": "m"}), ("finish", {"answer": "guess"})),
            reply(("finish", {"answer": "PAID"})),
        ]
    )
    api = FakeApi({"getClaim": ok("getClaim", {"status": "PAID"})})
    state, _ = await run(llm, api)
    assert state["answer"] == "PAID"
    assert any("call finish on its own" in m.get("content", "") for m in state["messages"])


async def test_a_question_is_not_finished_without_an_answer() -> None:
    llm = ScriptedLlm(
        [
            PLAN,
            reply(("getClaim", {"memberNumber": "m"})),
            reply(("finish", {"summary": "Found it."})),
            reply(("finish", {"answer": "PAID", "summary": "Found it."})),
        ]
    )
    state, _ = await run(llm, FakeApi({"getClaim": ok("getClaim", {"status": "PAID"})}))
    assert state["answer"] == "PAID"
    assert state["verified"] is True


async def test_a_call_written_as_text_is_recovered_when_it_is_exactly_one_tool_call() -> None:
    llm = ScriptedLlm(
        [
            PLAN,
            reply(content='getClaim: {"memberNumber": "m"}'),
            reply(content='finish: {"answer": "PAID"}'),
        ]
    )
    api = FakeApi({"getClaim": ok("getClaim", {"status": "PAID"})})
    state, _ = await run(llm, api)
    assert api.executed == ["getClaim"]
    assert state["answer"] == "PAID"
    assert state.get("nudges", 0) == 0


async def test_a_change_task_that_wrote_nothing_is_unverified() -> None:
    llm = ScriptedLlm([WRITE_PLAN, reply(("finish", {"summary": "Updated the email."}))])
    state, _ = await run(llm, FakeApi({"getMember": ok("getMember")}))
    assert state["outcome"] == "unverified"
    assert state["verified"] is False
