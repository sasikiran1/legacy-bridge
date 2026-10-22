"""Test wiring: the proxy with a fast regex detector, an in-memory vault and a recording provider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from proxy.app import Services
from proxy.audit import AuditLog
from proxy.detection import Detector, RegexDetector
from proxy.masking import Masker
from proxy.offline import RECORDING_MODEL, SYNTHETIC_TOKEN_KEY, offline_services
from proxy.policy import PiiPolicy, load_policy
from proxy.recording import RecordingProvider
from proxy.rehydrate import Rehydrator, ToolArgumentResolver
from proxy.tokens import TOKEN_RE, TokenMinter
from proxy.vault import MemoryVault, Sealer

VAULT_KEY = bytes(range(32))
TOKEN_KEY = SYNTHETIC_TOKEN_KEY
MOCK_MODEL = RECORDING_MODEL


def fast_detector() -> Detector:
    return RegexDetector(with_presidio_patterns=False)


@dataclass
class Kit:
    masker: Masker
    rehydrator: Rehydrator
    resolver: ToolArgumentResolver
    vault: MemoryVault
    audit: AuditLog
    policy: PiiPolicy


def kit(tmp_path: Path, detector: Detector | None = None) -> Kit:
    policy = load_policy()
    vault = MemoryVault(Sealer(VAULT_KEY))
    audit = AuditLog(tmp_path / "audit.jsonl")
    masker = Masker(detector or fast_detector(), TokenMinter(TOKEN_KEY, "7"), vault, audit, policy)
    return Kit(
        masker=masker,
        rehydrator=Rehydrator(vault, audit),
        resolver=ToolArgumentResolver(vault, audit, policy, frozenset({"portal", "localhost"})),
        vault=vault,
        audit=audit,
        policy=policy,
    )


def services(tmp_path: Path, provider: RecordingProvider) -> tuple[Services, MemoryVault]:
    vault = MemoryVault(Sealer(VAULT_KEY))
    built = offline_services(fast_detector(), provider, tmp_path / "audit.jsonl", vault=vault)
    return built, vault


def tokens_in(text: str) -> list[str]:
    return [m.group() for m in TOKEN_RE.finditer(text)]
