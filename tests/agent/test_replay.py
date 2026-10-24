from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.guardrails import spotlight
from agent.replay import CompileError, Compiler, Locator, load_script
from proxy.tokens import TokenMinter

MINTER = TokenMinter(b"k" * 32, "7")
TYPES = ["EMAIL_ADDRESS", "MEMBER_ID", "PERSON"]
TASK = "healthy/update_contact-09"
EMAIL = "new.address@example.org"


def token(entity_type: str, value: str) -> str:
    return MINTER.mint(TASK, entity_type, value).render()


def call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def write_cassettes(directory: Path, pages: list[str], calls: list[dict[str, Any]]) -> Path:
    """One exchange per call: each request ends with the page the previous call produced."""
    directory.mkdir()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Current page:\n" + spotlight(pages[0], "portal-page")}
    ]
    for index, tool_call in enumerate([*calls, call("done", "finish", {"summary": "done"})]):
        reply = {"role": "assistant", "content": "", "tool_calls": [tool_call]}
        exchange = {
            "request": {"messages": list(messages)},
            "body": json.dumps({"choices": [{"message": reply}]}),
        }
        (directory / f"{index:03d}.json").write_text(json.dumps(exchange), encoding="utf-8")
        if index < len(calls):
            messages += [
                reply,
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": spotlight(pages[index + 1], "portal-page"),
                },
            ]
    return directory


FORM = '- textbox "E-mail" [ref=e5]\n- textbox "Phone" [ref=e7]\n- button "Save contact details" [ref=e6]'


def compile_(directory: Path, params: dict[str, str]) -> Any:
    return Compiler(MINTER, TYPES).compile(directory, TASK, "update_contact", params, "M1000009")


def test_typed_tokens_become_parameters_and_the_script_round_trips(tmp_path: Path) -> None:
    cassettes = write_cassettes(
        tmp_path / "c",
        [FORM, FORM, "- status: Contact details saved."],
        [
            call("c0", "browser_type", {"target": "e5", "text": token("EMAIL_ADDRESS", EMAIL)}),
            call("c1", "browser_click", {"target": "e6"}),
        ],
    )
    script = compile_(cassettes, {"email": EMAIL})
    assert [s.value for s in script.steps] == ["${email}", None]
    assert script.steps[0].target == Locator(role="textbox", name="E-mail")
    assert EMAIL not in script.dump()
    (tmp_path / "s.yaml").write_text(script.dump(), encoding="utf-8")
    assert load_script(tmp_path / "s.yaml") == script


def test_a_value_from_the_source_run_is_never_compiled_in(tmp_path: Path) -> None:
    other = token("PERSON", "Ada Quinby")
    cassettes = write_cassettes(
        tmp_path / "c", [FORM, FORM], [call("c0", "browser_type", {"target": "e5", "text": other})]
    )
    with pytest.raises(CompileError, match="no parameter"):
        compile_(cassettes, {"email": EMAIL})


def test_a_parameter_the_source_run_never_wrote_is_a_compile_error(tmp_path: Path) -> None:
    cassettes = write_cassettes(
        tmp_path / "c",
        [FORM, FORM, "- status: Contact details saved."],
        [
            call("c0", "browser_type", {"target": "e5", "text": token("EMAIL_ADDRESS", EMAIL)}),
            call("c1", "browser_click", {"target": "e6"}),
        ],
    )
    with pytest.raises(CompileError, match="never wrote phone"):
        compile_(cassettes, {"email": EMAIL, "phone": "555-0100"})


def test_a_button_in_a_table_row_is_anchored_to_the_row_a_parameter_identifies(tmp_path: Path) -> None:
    note = "Member asked for statements by email going forward."
    table = (
        "- row [ref=e10]:\n"
        f'  - cell "{note}" [ref=e11]\n'
        "  - cell [ref=e12]:\n"
        '    - button "Delete" [ref=e13]\n'
    )
    cassettes = write_cassettes(
        tmp_path / "c", [table, "- status: Note deleted."], [call("c0", "browser_click", {"target": "e13"})]
    )
    script = Compiler(MINTER, TYPES).compile(cassettes, TASK, "delete_note", {"note_body": note}, "M1000009")
    assert script.steps[0].target == Locator(role="button", name="Delete", within_row="note_body")
