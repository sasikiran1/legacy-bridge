from __future__ import annotations

from agent.executors import _REF, element_index, page_state, trim_snapshot

SNAPSHOT = """### Page
- Page URL: http://portal:8080/members/M1
### Snapshot
```yaml
- main [ref=f3e11]:
  - heading "Member Record: [[PERSON:0a1b2c3d4e]]" [level=1] [ref=f3e12]
  - textbox "Street" [ref=f3e30]
  - button "Save address" [ref=e31] [cursor=pointer]
```"""


def test_element_index_reads_refs_from_any_snapshot_generation() -> None:
    index = element_index(page_state(SNAPSHOT))
    assert index["f3e30"] == ("textbox", "Street")
    assert index["e31"] == ("button", "Save address")


def test_only_snapshot_refs_are_accepted_as_targets() -> None:
    assert _REF.fullmatch("e12")
    assert _REF.fullmatch("f10e25")
    assert not _REF.fullmatch("button")
    assert not _REF.fullmatch("#save")


MEMBER_PAGE = """- Page URL: http://portal:8080/members/x
- Console: 1 errors, 0 warnings
- generic [ref=e3]:
  - main [ref=e11]:
    - 'heading "Member Record" [level=1] [ref=e12]'
    - generic [ref=e13]:
      - textbox "E-mail" [ref=e14]
      - button "Save" [ref=e15] [cursor=pointer]
    - link "Claims" [ref=e16] [cursor=pointer]:
      - /url: /claims
  - contentinfo [ref=e20]: Member Services Portal 4.2.7"""


def test_trimming_keeps_actionable_refs_and_drops_noise() -> None:
    trimmed = trim_snapshot(MEMBER_PAGE)
    assert trimmed == (
        "- Page URL: http://portal:8080/members/x\n"
        "- main:\n"
        "  - 'heading \"Member Record\"'\n"
        '  - textbox "E-mail" [ref=e14]\n'
        '  - button "Save" [ref=e15]\n'
        '  - link "Claims" [ref=e16]:\n'
        "    - /url: /claims"
    )
    assert set(element_index(MEMBER_PAGE)) >= {"e14", "e15", "e16"}
