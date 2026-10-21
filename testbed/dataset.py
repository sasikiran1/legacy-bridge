"""Schemas for the synthetic portal dataset. Field names mirror the portal's database columns."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Role(StrEnum):
    CLERK = "CLERK"
    SUPERVISOR = "SUPERVISOR"


class PolicyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LAPSED = "LAPSED"
    CANCELLED = "CANCELLED"


class ClaimStatus(StrEnum):
    RECEIVED = "RECEIVED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    PAID = "PAID"


class EntityType(StrEnum):
    """Entity types used for canaries; the proxy's PII policy (M2) uses the same names."""

    PERSON = "PERSON"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    US_SSN = "US_SSN"
    EMAIL_ADDRESS = "EMAIL_ADDRESS"
    PHONE_NUMBER = "PHONE_NUMBER"
    STREET_ADDRESS = "STREET_ADDRESS"
    MEMBER_ID = "MEMBER_ID"
    POLICY_NUMBER = "POLICY_NUMBER"
    CLAIM_NUMBER = "CLAIM_NUMBER"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    ROUTING_NUMBER = "ROUTING_NUMBER"


class PortalUser(Frozen):
    id: int
    username: str
    display_name: str
    role: Role


class Member(Frozen):
    id: int
    member_number: str
    first_name: str
    last_name: str
    date_of_birth: dt.date
    ssn: str
    email: str
    phone: str
    street: str
    city: str
    state: str
    postal_code: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Policy(Frozen):
    id: int
    policy_number: str
    member_id: int
    product: str
    status: PolicyStatus
    effective_date: dt.date
    monthly_premium: Decimal


class Claim(Frozen):
    id: int
    claim_number: str
    policy_id: int
    service_date: dt.date
    provider_name: str
    amount: Decimal
    status: ClaimStatus


class Note(Frozen):
    id: int
    member_id: int
    author: str
    body: str
    created_at: dt.datetime
    injected: bool


class Injection(Frozen):
    """A planted prompt-injection string; see testbed/data/injections.yaml."""

    id: str
    split: Literal["dev", "test"]
    goal: str
    body: str


class Canary(Frozen):
    """A planted value. Any appearance outside the host trust boundary is a leak."""

    entity_type: EntityType
    value: str
    member_number: str


class Dataset(Frozen):
    seed: int
    users: tuple[PortalUser, ...]
    members: tuple[Member, ...]
    policies: tuple[Policy, ...]
    claims: tuple[Claim, ...]
    notes: tuple[Note, ...]
    canary_member_numbers: tuple[str, ...]

    def member(self, member_id: int) -> Member:
        return next(m for m in self.members if m.id == member_id)

    def policies_of(self, member: Member) -> list[Policy]:
        return [p for p in self.policies if p.member_id == member.id]

    def claims_of(self, member: Member) -> list[Claim]:
        policy_ids = {p.id for p in self.policies_of(member)}
        return [c for c in self.claims if c.policy_id in policy_ids]

    def notes_of(self, member: Member) -> list[Note]:
        return [n for n in self.notes if n.member_id == member.id]

    def owner_of_claim(self, claim: Claim) -> Member:
        policy = next(p for p in self.policies if p.id == claim.policy_id)
        return self.member(policy.member_id)
