from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from proxy.audit import verify
from proxy.rehydrate import RehydrationDenied, StreamRehydrator
from tests.proxy.fakes import Kit, kit

SOURCE = (
    "Member M1234567 lives at 12 Harbor Way; claim CLM-2025-104233. "
    "Literal [[ bracket, [[NOT:token]] and JSON [[1, 2], [3]] stay."
)


async def _masked(k: Kit) -> str:
    return await k.masker.mask_text(SOURCE, "t1", "groq")


async def _stream(k: Kit, chunks: list[str]) -> tuple[str, list[str]]:
    stream = StreamRehydrator(k.rehydrator, "t1", "client")
    emitted = [await stream.feed(chunk) for chunk in chunks]
    emitted.append(await stream.finish())
    return "".join(emitted), emitted


async def test_every_single_split_point(tmp_path: Path) -> None:
    k = kit(tmp_path)
    masked = await _masked(k)
    expected = await k.rehydrator.text(masked, "t1", "client")
    assert expected == SOURCE
    for cut in range(len(masked) + 1):
        joined, emitted = await _stream(k, [masked[:cut], masked[cut:]])
        assert joined == expected, cut
        assert not any("[[MEMBER_ID" in piece or "[[CLAIM_NUMBER" in piece for piece in emitted), cut


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(cuts=st.lists(st.integers(min_value=0, max_value=200), max_size=30))
async def test_arbitrary_chunkings(tmp_path: Path, cuts: list[int]) -> None:
    k = kit(tmp_path)
    masked = await _masked(k)
    points = sorted({min(c, len(masked)) for c in cuts})
    chunks = [masked[a:b] for a, b in zip([0, *points], [*points, len(masked)], strict=True)]
    joined, _ = await _stream(k, chunks)
    assert joined == SOURCE


async def test_unclosed_bracket_is_released_at_the_end(tmp_path: Path) -> None:
    k = kit(tmp_path)
    joined, _ = await _stream(k, ["value [[PERS", "ON:12"])
    assert joined == "value [[PERSON:12"


async def test_resolver_restores_allowed_arguments(tmp_path: Path) -> None:
    k = kit(tmp_path)
    member = await k.masker.mask_text("M1234567", "t1", "agent")
    resolved = await k.resolver.resolve("t1", "getMember", {"memberNumber": member}, target_host="portal")
    assert resolved == {"memberNumber": "M1234567"}


async def test_resolver_refuses_a_type_the_argument_does_not_allow(tmp_path: Path) -> None:
    k = kit(tmp_path)
    ssn = await k.masker.mask_text("SSN 123-45-6789", "t1", "agent")
    with pytest.raises(RehydrationDenied, match="US_SSN"):
        await k.resolver.resolve(
            "t1", "addNote", {"memberNumber": "M1234567", "body": f"verification: {ssn}"}
        )
    assert '"action":"unmask_denied"' in k.audit.path.read_text(encoding="utf-8")
    assert verify(k.audit.path).ok


async def test_resolver_refuses_tokens_from_another_task(tmp_path: Path) -> None:
    k = kit(tmp_path)
    member = await k.masker.mask_text("M1234567", "t1", "agent")
    with pytest.raises(RehydrationDenied, match="not a token of this task"):
        await k.resolver.resolve("t2", "getMember", {"memberNumber": member})


async def test_resolver_refuses_hosts_off_the_allowlist(tmp_path: Path) -> None:
    k = kit(tmp_path)
    with pytest.raises(RehydrationDenied, match="allowlist"):
        await k.resolver.resolve("t1", "getMember", {"memberNumber": "x"}, target_host="evil.example.com")


async def test_credentials_are_never_rehydrated(tmp_path: Path) -> None:
    k = kit(tmp_path)
    masked = await k.masker.mask_content('{"password": "hunter2-hunter2"}', "t1", "agent")
    token = masked.split('"')[3]
    with pytest.raises(RehydrationDenied):
        await k.resolver.resolve("t1", "addNote", {"body": token})


async def test_ui_fields_receive_only_the_types_their_label_allows(tmp_path: Path) -> None:
    k = kit(tmp_path)
    account = await k.masker.mask_text("account number 4400917733", "t1", "agent")
    token = account.split()[-1]
    assert await k.resolver.resolve_field("t1", "browser_type", "Bank account number", token) == "4400917733"
    with pytest.raises(RehydrationDenied):
        await k.resolver.resolve_field("t1", "browser_type", "New note", token)
    with pytest.raises(RehydrationDenied):
        await k.resolver.resolve_field("t1", "browser_type", None, token)


async def test_near_miss_placeholders_are_refused_not_typed(tmp_path: Path) -> None:
    k = kit(tmp_path)
    member = await k.masker.mask_text("M1234567", "t1", "agent")
    with pytest.raises(RehydrationDenied, match="malformed placeholder"):
        await k.resolver.resolve("t1", "getMember", {"memberNumber": member[:-1]})
    with pytest.raises(RehydrationDenied, match="malformed placeholder"):
        await k.resolver.resolve_field("t1", "browser_type", "Member number", member[1:])
    assert await k.resolver.resolve("t1", "addNote", {"body": "Ref ID:1234 per call"}) == {
        "body": "Ref ID:1234 per call"
    }
