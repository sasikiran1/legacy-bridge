"""Unlabeled inputs take their label from the caption a person would read beside or above them."""

from __future__ import annotations

from agent.executors import LayoutElement, label_for


def text(label: str, x: int, y: int, w: int = 170) -> LayoutElement:
    return LayoutElement(kind="text", x=x, y=y, w=w, h=22, text=label)


def field(x: int, y: int) -> LayoutElement:
    return LayoutElement(kind="input", x=x, y=y, w=226, h=22)


FORM = [
    text("Member number", 171, 112),
    field(360, 112),
    text("Bank routing number", 171, 216),
    field(360, 216),
    text("Bank account number", 171, 242),
    field(360, 242),
]


def test_the_caption_on_the_same_row_labels_the_input() -> None:
    assert label_for(FORM[1], FORM) == "Member number"
    assert label_for(FORM[5], FORM) == "Bank account number"


def test_a_caption_above_is_used_when_nothing_is_beside_it() -> None:
    layout = [text("Payee name", 20, 300), field(20, 324)]
    assert label_for(layout[1], layout) == "Payee name"


def test_text_to_the_right_or_on_another_row_is_not_a_label() -> None:
    layout = [field(20, 112), text("Member number", 300, 112), text("Claim number", 20, 400)]
    assert label_for(layout[0], layout) is None
