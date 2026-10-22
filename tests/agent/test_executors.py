from __future__ import annotations

from agent.executors import _REF, element_index, page_state

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
