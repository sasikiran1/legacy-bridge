"""Wire the proxy to a recording provider instead of a real one (leak check, benchmarks, demo, tests)."""

from __future__ import annotations

from pathlib import Path

import httpx

from proxy.app import Services
from proxy.audit import AuditLog
from proxy.detection import Detector
from proxy.masking import Masker
from proxy.policy import PiiPolicy, load_policy
from proxy.providers import Provider, Registry
from proxy.recording import RecordingProvider
from proxy.rehydrate import Rehydrator
from proxy.tokens import TokenMinter
from proxy.vault import MemoryVault, Sealer, Vault

RECORDING_MODEL = "recording-model"
SYNTHETIC_TOKEN_KEY = b"legacy-bridge synthetic token k1"
"""The published token key from .env.example (ADR 0001); fine only because all PII here is synthetic."""


def offline_services(
    detector: Detector,
    provider: RecordingProvider,
    audit_path: Path,
    vault: Vault | None = None,
    policy: PiiPolicy | None = None,
    vault_key: bytes = bytes(range(32)),
) -> Services:
    policy = policy or load_policy()
    vault = vault or MemoryVault(Sealer(vault_key))
    audit = AuditLog(audit_path)
    masker = Masker(detector, TokenMinter(SYNTHETIC_TOKEN_KEY, "7"), vault, audit, policy)
    registry = Registry(
        {
            "recording": Provider(
                name="recording",
                base_url="http://recording.invalid/v1",
                api_key=None,
                trust="remote_untrusted",
            )
        },
        {RECORDING_MODEL: "recording"},
    )
    return Services(
        masker=masker,
        rehydrator=Rehydrator(vault, audit),
        vault=vault,
        registry=registry,
        http=httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
        audit=audit,
    )
