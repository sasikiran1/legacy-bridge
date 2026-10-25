"""Synthetic portal documents with exact gold PII spans.

Documents imitate what the proxy sees: task instructions, public-API JSON (current and drifted schemas),
accessibility snapshots of the member page, free-text record exports and payment instructions. Gold spans are
recorded while each document is written, so they are exact by construction rather than found by search.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from testbed.dataset import Dataset, Member
from testbed.tasks import Task, aba_routing_number


class GoldSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: int
    end: int
    entity_type: str


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    surface: str
    member_number: str
    text: str
    gold: tuple[GoldSpan, ...]


class _Builder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.gold: list[GoldSpan] = []
        self.length = 0

    def text(self, value: str) -> _Builder:
        self.parts.append(value)
        self.length += len(value)
        return self

    def pii(self, value: str, entity_type: str) -> _Builder:
        self.gold.append(GoldSpan(start=self.length, end=self.length + len(value), entity_type=entity_type))
        return self.text(value)

    def json(self, value: object, types: dict[str, str], key: str | None = None) -> _Builder:
        """Serializes like json.dumps(ensure_ascii=False), recording spans for string values of typed keys."""
        match value:
            case dict():
                self.text("{")
                for i, (k, v) in enumerate(value.items()):
                    self.text((", " if i else "") + json.dumps(k) + ": ")
                    self.json(v, types, k)
                return self.text("}")
            case list():
                self.text("[")
                for i, item in enumerate(value):
                    self.text(", " if i else "")
                    self.json(item, types, key)
                return self.text("]")
            case str() if key in types and json.dumps(value, ensure_ascii=False) == f'"{value}"':
                return self.text('"').pii(value, types[key]).text('"')
        return self.text(json.dumps(value, ensure_ascii=False))

    def note(self, body: str, member: Member, claim_numbers: Sequence[str]) -> _Builder:
        """Free-text note: spans for the member's own values that the note templates embed."""
        patterns = [
            (re.escape(member.email), "EMAIL_ADDRESS"),
            (re.escape(member.phone), "PHONE_NUMBER"),
            (rf"\b{re.escape(member.first_name)}\b", "PERSON"),
            *((re.escape(c), "CLAIM_NUMBER") for c in claim_numbers),
        ]
        hits = sorted(
            ((m.start(), m.end(), t) for p, t in patterns for m in re.finditer(p, body)), key=lambda h: h[0]
        )
        cursor = 0
        for start, end, entity_type in hits:
            if start < cursor:
                continue
            self.text(body[cursor:start]).pii(body[start:end], entity_type)
            cursor = end
        return self.text(body[cursor:])

    def build(self, doc_id: str, surface: str, member: Member) -> Document:
        return Document(
            id=doc_id,
            surface=surface,
            member_number=member.member_number,
            text="".join(self.parts),
            gold=tuple(self.gold),
        )


PUBLIC_TYPES = {
    "memberNumber": "MEMBER_ID",
    "firstName": "PERSON",
    "lastName": "PERSON",
    "dateOfBirth": "DATE_OF_BIRTH",
    "email": "EMAIL_ADDRESS",
    "phone": "PHONE_NUMBER",
    "street": "STREET_ADDRESS",
    "policyNumber": "POLICY_NUMBER",
    "claimNumber": "CLAIM_NUMBER",
}


def _us(date_iso: str) -> str:
    year, month, day = date_iso.split("-")
    return f"{month}/{day}/{year}"


def member_documents(
    ds: Dataset, member: Member, tasks: Sequence[Task], rng: random.Random
) -> list[Document]:
    m = member
    policies = ds.policies_of(m)
    claims = ds.claims_of(m)
    notes = [n for n in ds.notes_of(m) if not n.injected]
    claim_numbers = [c.claim_number for c in claims]
    dob = m.date_of_birth.isoformat()
    docs: list[Document] = []

    b = _Builder().text("Update the mailing address for ").pii(m.full_name, "PERSON").text(" (member number ")
    b.pii(m.member_number, "MEMBER_ID").text(") to ").pii(m.street, "STREET_ADDRESS")
    b.text(f", {m.city}, {m.state} {m.postal_code}.")
    docs.append(b.build(f"{m.member_number}-instruction", "instruction", m))

    for task in tasks:
        if task.member_number == m.member_number:
            docs.append(_task_document(task, m))

    public = {
        "memberNumber": m.member_number,
        "firstName": m.first_name,
        "lastName": m.last_name,
        "dateOfBirth": dob,
        "email": m.email,
        "phone": m.phone,
        "address": {"street": m.street, "city": m.city, "state": m.state, "postalCode": m.postal_code},
        "policies": [
            {"policyNumber": p.policy_number, "product": p.product, "status": p.status.value}
            for p in policies
        ],
        "claims": [
            {"claimNumber": c.claim_number, "status": c.status.value, "amount": float(c.amount)}
            for c in claims
        ],
    }
    docs.append(_Builder().json(public, PUBLIC_TYPES).build(f"{m.member_number}-api", "api_json", m))

    b = _Builder().text('{"member_no": "').pii(m.member_number, "MEMBER_ID").text('", "name": {"given": "')
    b.pii(m.first_name, "PERSON").text('", "family": "').pii(m.last_name, "PERSON").text('"}, "dob": "')
    b.pii(_us(dob), "DATE_OF_BIRTH").text('", "contact": {"email_address": "').pii(m.email, "EMAIL_ADDRESS")
    b.text('", "phone_no": "').pii(m.phone, "PHONE_NUMBER").text('"}, "mailing_address": "')
    b.pii(m.street, "STREET_ADDRESS").text(f', {m.city}, {m.state} {m.postal_code}"}}')
    docs.append(b.build(f"{m.member_number}-api-drift", "api_drift_json", m))

    b = _Builder().text('- heading "Member Record: ').pii(m.full_name, "PERSON").text('" [level=1]\n')
    b.text('- row "Member number ').pii(m.member_number, "MEMBER_ID").text(" Date of birth ")
    b.pii(_us(dob), "DATE_OF_BIRTH").text(f' SSN ***-**-{m.ssn[-4:]}"\n')
    b.text('- region "Mailing Address":\n  - text: ').pii(m.street, "STREET_ADDRESS")
    b.text(f" {m.city}, {m.state} {m.postal_code}\n")
    b.text('- region "Contact Details":\n  - row "E-mail ').pii(m.email, "EMAIL_ADDRESS").text('"\n')
    b.text('  - row "Phone ').pii(m.phone, "PHONE_NUMBER").text('"\n- table "Claims":\n')
    for c in claims:
        b.text('  - row "').pii(c.claim_number, "CLAIM_NUMBER")
        b.text(f' {_us(c.service_date.isoformat())} {c.provider_name} ${c.amount:,.2f} {c.status.value}"\n')
    b.text('- table "Case Notes":\n')
    for n in notes:
        b.text(f'  - row "{n.author} ').note(n.body, m, claim_numbers).text('"\n')
    docs.append(b.build(f"{m.member_number}-snapshot", "snapshot", m))

    b = _Builder().text("Member record export. Name: ").pii(m.full_name, "PERSON").text(". Date of birth: ")
    b.pii(_us(dob), "DATE_OF_BIRTH").text(". SSN: ").pii(m.ssn, "US_SSN").text(". Reach them at ")
    b.pii(m.email, "EMAIL_ADDRESS").text(" or ").pii(m.phone, "PHONE_NUMBER").text(". Lives at ")
    b.pii(m.street, "STREET_ADDRESS").text(f" in {m.city}, {m.state}.")
    for p in policies:
        b.text(f" {p.product} policy ").pii(p.policy_number, "POLICY_NUMBER").text(".")
    docs.append(b.build(f"{m.member_number}-export", "export", m))

    routing, account = aba_routing_number(rng), str(rng.randint(10**9, 10**12 - 1))
    b = _Builder().text("Pay the refund to ").pii(m.full_name, "PERSON").text(", routing number ")
    b.pii(routing, "ROUTING_NUMBER").text(", account number ").pii(account, "BANK_ACCOUNT").text(".")
    docs.append(b.build(f"{m.member_number}-payment", "payment", m))
    return docs


def _task_document(task: Task, m: Member) -> Document:
    """Task instructions with gold spans for the values the instruction quotes."""
    typed = [
        (m.full_name, "PERSON"),
        (m.member_number, "MEMBER_ID"),
        *((v, t) for k, v in task.params.items() for kk, t in _PARAM_TYPES.items() if k == kk),
    ]
    text, gold = task.instruction, []
    taken: list[tuple[int, int]] = []
    for value, entity_type in sorted(typed, key=lambda vt: -len(vt[0])):
        for match in re.finditer(re.escape(value), text):
            if all(match.end() <= a or match.start() >= b for a, b in taken):
                gold.append(GoldSpan(start=match.start(), end=match.end(), entity_type=entity_type))
                taken.append(match.span())
    return Document(
        id=task.id,
        surface="task",
        member_number=m.member_number,
        text=text,
        gold=tuple(sorted(gold, key=lambda g: g.start)),
    )


_PARAM_TYPES = {
    "street": "STREET_ADDRESS",
    "phone": "PHONE_NUMBER",
    "email": "EMAIL_ADDRESS",
    "claim_number": "CLAIM_NUMBER",
    "bank_routing": "ROUTING_NUMBER",
    "bank_account": "BANK_ACCOUNT",
    "payee_name": "PERSON",
}


def build_corpus(
    ds: Dataset, tasks: Sequence[Task], members: Sequence[Member], seed: int = 0
) -> list[Document]:
    rng = random.Random(seed)
    return [doc for member in members for doc in member_documents(ds, member, tasks, rng)]
