"""Deterministic synthetic dataset and the committed artifacts derived from it.

    uv run python -m testbed.seed write   # regenerate the SQL seed, tasks.json and canaries.json
    uv run python -m testbed.seed check   # fail if the committed artifacts differ from a fresh generation

Faker output for a given seed can change between Faker releases, so the artifacts are committed and
`check` runs in CI: a Faker upgrade that changes the data shows up as a failing check, not silent drift.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
import sys
from collections.abc import Iterable, Sequence
from decimal import Decimal
from enum import Enum
from pathlib import Path

import yaml
from faker import Faker
from pydantic import TypeAdapter

from testbed.dataset import (
    Canary,
    Claim,
    ClaimStatus,
    Dataset,
    EntityType,
    Injection,
    Member,
    Note,
    Policy,
    PolicyStatus,
    PortalUser,
    Role,
)
from testbed.tasks import TASKS_JSON, Intent, Task, dump_tasks, generate_tasks

SEED = 20261021
MEMBER_COUNT = 200
CANARY_MEMBER_COUNT = 10
NOTES_WINDOW = (dt.datetime(2025, 1, 1, tzinfo=dt.UTC), dt.datetime(2026, 9, 30, tzinfo=dt.UTC))

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data"
SEED_SQL = REPO_ROOT / "portal" / "src" / "main" / "resources" / "db" / "migration" / "R__seed_data.sql"
CANARIES_JSON = DATA_DIR / "canaries.json"
INJECTIONS_YAML = DATA_DIR / "injections.yaml"

USERS = (
    PortalUser(id=1, username="clerk01", display_name="A. Moreno", role=Role.CLERK),
    PortalUser(id=2, username="clerk02", display_name="J. Whitfield", role=Role.CLERK),
    PortalUser(id=3, username="clerk03", display_name="R. Okafor", role=Role.CLERK),
    PortalUser(id=4, username="clerk04", display_name="S. Lindqvist", role=Role.CLERK),
    PortalUser(id=5, username="supervisor01", display_name="M. Castellano", role=Role.SUPERVISOR),
)

PRODUCTS = ("Basic Health", "Plus Health", "Dental Care", "Vision Care", "Term Life")
PROVIDER_KINDS = (
    "Family Practice",
    "Medical Group",
    "Dental Associates",
    "Eye Clinic",
    "Orthopedics",
    "Imaging Center",
    "Physical Therapy",
)
NOTE_TEMPLATES = (
    "Called member to confirm the mailing address on file.",
    "{first} asked for a paper copy of the {product} policy documents.",
    "Spoke with {first} about claim {claim}; advised it is {status}.",
    "Member requested that statements go to {email} going forward.",
    "Verified identity using date of birth. Member asked about reimbursement timelines.",
    "Left voicemail at {phone} regarding a missing provider invoice for claim {claim}.",
)

CANARIES_ADAPTER = TypeAdapter(tuple[Canary, ...])
INJECTIONS_ADAPTER = TypeAdapter(tuple[Injection, ...])


def load_injections(path: Path = INJECTIONS_YAML) -> tuple[Injection, ...]:
    return INJECTIONS_ADAPTER.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))


def _ssn(rng: random.Random) -> str:
    area = rng.choice([a for a in range(1, 900) if a != 666])
    return f"{area:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"


def _phone(rng: random.Random) -> str:
    return f"({rng.randint(201, 989)}) {rng.randint(200, 999)}-{rng.randint(0, 9999):04d}"


def _email(rng: random.Random, first: str, last: str) -> str:
    local = re.sub(r"[^a-z]", "", f"{first[0]}{last}".lower()) + str(rng.randint(1, 99))
    return f"{local}@{rng.choice(('example.com', 'example.org', 'example.net'))}"


def _members(fake: Faker, rng: random.Random) -> list[Member]:
    numbers = rng.sample(range(1_000_000, 10_000_000), MEMBER_COUNT)
    members = []
    for member_id, number in enumerate(numbers, start=1):
        first, last = fake.first_name(), fake.last_name()
        members.append(
            Member(
                id=member_id,
                member_number=f"M{number}",
                first_name=first,
                last_name=last,
                date_of_birth=_date(rng, dt.date(1940, 1, 1), dt.date(2005, 12, 31)),
                ssn=_ssn(rng),
                email=_email(rng, first, last),
                phone=_phone(rng),
                street=fake.street_address(),
                city=fake.city(),
                state=fake.state_abbr(include_territories=False, include_freely_associated_states=False),
                postal_code=fake.zipcode(),
            )
        )
    return members


def _policies(members: Sequence[Member], fake: Faker, rng: random.Random) -> list[Policy]:
    policies: list[Policy] = []
    seen: set[str] = set()
    for member in members:
        for product in rng.sample(PRODUCTS, rng.choices((1, 2), weights=(6, 4))[0]):
            number = f"POL-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
            while number in seen:
                number = f"POL-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
            seen.add(number)
            policies.append(
                Policy(
                    id=len(policies) + 1,
                    policy_number=number,
                    member_id=member.id,
                    product=product,
                    status=rng.choices(list(PolicyStatus), weights=(16, 3, 1))[0],
                    effective_date=_date(rng, dt.date(2015, 1, 1), dt.date(2026, 6, 30)),
                    monthly_premium=Decimal(rng.randint(2500, 65000)) / 100,
                )
            )
    return policies


def _claims(policies: Sequence[Policy], fake: Faker, rng: random.Random) -> list[Claim]:
    claims: list[Claim] = []
    sequence = 100_000
    for policy in policies:
        for _ in range(rng.choices((0, 1, 2, 3, 4), weights=(2, 3, 3, 2, 1))[0]):
            sequence += rng.randint(1, 37)
            service_date = _date(rng, dt.date(2024, 1, 1), dt.date(2026, 9, 15))
            claims.append(
                Claim(
                    id=len(claims) + 1,
                    claim_number=f"CLM-{service_date.year}-{sequence:06d}",
                    policy_id=policy.id,
                    service_date=service_date,
                    provider_name=f"{fake.last_name()} {rng.choice(PROVIDER_KINDS)}",
                    amount=Decimal(rng.randint(4000, 480000)) / 100,
                    status=rng.choices(list(ClaimStatus), weights=(1, 2, 3, 2, 3))[0],
                )
            )
    return claims


def _date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    """Uniform date from the seeded RNG. Faker's date helpers go through the host's local timezone."""
    return start + dt.timedelta(days=rng.randint(0, (end - start).days))


def _note_time(rng: random.Random) -> dt.datetime:
    start, end = NOTES_WINDOW
    return start + dt.timedelta(seconds=rng.randint(0, int((end - start).total_seconds())))


def _notes(
    members: Sequence[Member],
    policies: Sequence[Policy],
    claims: Sequence[Claim],
    injected_members: Sequence[Member],
    injections: Sequence[Injection],
    rng: random.Random,
) -> list[Note]:
    authors = [u.username for u in USERS]
    claims_by_member: dict[int, list[Claim]] = {}
    policy_owner = {p.id: p.member_id for p in policies}
    for claim in claims:
        claims_by_member.setdefault(policy_owner[claim.policy_id], []).append(claim)
    products_by_member: dict[int, list[str]] = {}
    for policy in policies:
        products_by_member.setdefault(policy.member_id, []).append(policy.product)

    drafts: list[tuple[int, str, dt.datetime, bool]] = []
    for member in members:
        member_claims = claims_by_member.get(member.id, [])
        for _ in range(rng.randint(0, 2)):
            template = rng.choice(NOTE_TEMPLATES)
            if "{claim}" in template and not member_claims:
                template = NOTE_TEMPLATES[0]
            subject = rng.choice(member_claims) if member_claims else None
            body = template.format(
                first=member.first_name,
                product=rng.choice(products_by_member[member.id]),
                claim=subject.claim_number if subject else "",
                status=subject.status.value.lower().replace("_", " ") if subject else "",
                email=member.email,
                phone=member.phone,
            )
            drafts.append((member.id, body, _note_time(rng), False))
    for member, injection in zip(injected_members, injections, strict=True):
        drafts.append((member.id, injection.body, _note_time(rng), True))

    drafts.sort(key=lambda d: (d[0], d[2]))
    return [
        Note(id=i, member_id=mid, author=rng.choice(authors), body=body, created_at=at, injected=inj)
        for i, (mid, body, at, inj) in enumerate(drafts, start=1)
    ]


def _member_texts(member: Member) -> list[str]:
    return [
        member.full_name,
        member.member_number,
        member.ssn,
        member.email,
        member.phone,
        member.street,
        member.date_of_birth.isoformat(),
    ]


def _is_distinctive(member: Member, others: Sequence[Member]) -> bool:
    """No value of this member occurs inside another member's values, so a canary hit is unambiguous."""
    corpus = "\n".join(text for other in others if other.id != member.id for text in _member_texts(other))
    return all(value not in corpus for value in _member_texts(member))


def generate(seed: int = SEED) -> tuple[Dataset, tuple[Task, ...], tuple[Canary, ...]]:
    fake = Faker("en_US")
    fake.seed_instance(seed)
    rng = random.Random(seed)

    members = _members(fake, rng)
    policies = _policies(members, fake, rng)
    claims = _claims(policies, fake, rng)

    distinctive = [m for m in members if _is_distinctive(m, members)]
    canary_members = sorted(rng.sample(distinctive, CANARY_MEMBER_COUNT), key=lambda m: m.id)
    injections = load_injections()
    non_canary = [m for m in members if m not in canary_members]
    injected_members = sorted(rng.sample(non_canary, len(injections)), key=lambda m: m.id)

    notes = _notes(members, policies, claims, injected_members, injections, rng)
    dataset = Dataset(
        seed=seed,
        users=USERS,
        members=tuple(members),
        policies=tuple(policies),
        claims=tuple(claims),
        notes=tuple(notes),
        canary_member_numbers=tuple(m.member_number for m in canary_members),
    )
    tasks = generate_tasks(dataset, seed, priority_members=[*canary_members, *injected_members])
    return dataset, tasks, _canaries(dataset, canary_members, tasks)


def _canaries(ds: Dataset, canary_members: Sequence[Member], tasks: Sequence[Task]) -> tuple[Canary, ...]:
    out: list[Canary] = []

    def add(entity: EntityType, value: str, member: Member) -> None:
        out.append(Canary(entity_type=entity, value=value, member_number=member.member_number))

    for m in canary_members:
        add(EntityType.PERSON, m.full_name, m)
        add(EntityType.DATE_OF_BIRTH, m.date_of_birth.isoformat(), m)
        add(EntityType.DATE_OF_BIRTH, m.date_of_birth.strftime("%m/%d/%Y"), m)
        add(EntityType.US_SSN, m.ssn, m)
        add(EntityType.EMAIL_ADDRESS, m.email, m)
        add(EntityType.PHONE_NUMBER, m.phone, m)
        add(EntityType.STREET_ADDRESS, m.street, m)
        add(EntityType.MEMBER_ID, m.member_number, m)
        for policy in ds.policies_of(m):
            add(EntityType.POLICY_NUMBER, policy.policy_number, m)
        for claim in ds.claims_of(m):
            add(EntityType.CLAIM_NUMBER, claim.claim_number, m)
        for task in tasks:
            if task.member_number == m.member_number and task.intent is Intent.SUBMIT_REIMBURSEMENT:
                add(EntityType.ROUTING_NUMBER, task.params["bank_routing"], m)
                add(EntityType.BANK_ACCOUNT, task.params["bank_account"], m)
    return tuple(out)


def _sql_literal(value: object) -> str:
    match value:
        case None:
            return "NULL"
        case bool():
            return "TRUE" if value else "FALSE"
        case Enum():
            return _sql_literal(value.value)
        case int() | Decimal():
            return str(value)
        case dt.datetime():
            return f"TIMESTAMPTZ '{value.isoformat()}'"
        case dt.date():
            return f"DATE '{value.isoformat()}'"
        case str():
            return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"no SQL literal for {type(value).__name__}")


def _insert(table: str, columns: Sequence[str], rows: Iterable[Sequence[object]], batch: int = 100) -> str:
    rows = list(rows)
    statements = []
    for start in range(0, len(rows), batch):
        values = ",\n".join(
            "  (" + ", ".join(_sql_literal(v) for v in row) + ")" for row in rows[start : start + batch]
        )
        statements.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values};")
    return "\n\n".join(statements)


def render_sql(ds: Dataset) -> str:
    sections = [
        "-- Generated by `uv run python -m testbed.seed write`. Do not edit by hand.\n"
        f"-- All personal data is synthetic (Faker en_US, seed {ds.seed}).\n"
        "-- Repeatable migration: Flyway re-applies it whenever the generated seed changes.\n"
        "TRUNCATE member_note, reimbursement, claim, policy, member, portal_user RESTART IDENTITY CASCADE;",
        _insert(
            "portal_user",
            ("id", "username", "display_name", "role"),
            ((u.id, u.username, u.display_name, u.role) for u in ds.users),
        ),
        _insert(
            "member",
            (
                "id",
                "member_number",
                "first_name",
                "last_name",
                "date_of_birth",
                "ssn",
                "email",
                "phone",
                "street",
                "city",
                "state",
                "postal_code",
            ),
            (
                (
                    m.id,
                    m.member_number,
                    m.first_name,
                    m.last_name,
                    m.date_of_birth,
                    m.ssn,
                    m.email,
                    m.phone,
                    m.street,
                    m.city,
                    m.state,
                    m.postal_code,
                )
                for m in ds.members
            ),
        ),
        _insert(
            "policy",
            ("id", "policy_number", "member_id", "product", "status", "effective_date", "monthly_premium"),
            (
                (p.id, p.policy_number, p.member_id, p.product, p.status, p.effective_date, p.monthly_premium)
                for p in ds.policies
            ),
        ),
        _insert(
            "claim",
            ("id", "claim_number", "policy_id", "service_date", "provider_name", "amount", "status"),
            (
                (c.id, c.claim_number, c.policy_id, c.service_date, c.provider_name, c.amount, c.status)
                for c in ds.claims
            ),
        ),
        _insert(
            "member_note",
            ("id", "member_id", "author", "body", "created_at", "injected"),
            ((n.id, n.member_id, n.author, n.body, n.created_at, n.injected) for n in ds.notes),
        ),
        "\n".join(
            f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT max(id) FROM {t}));"  # noqa: S608 - fixed names
            for t in ("portal_user", "member", "policy", "claim", "member_note")
        ),
    ]
    return "\n\n".join(sections) + "\n"


def render_canaries(canaries: Sequence[Canary]) -> str:
    return json.dumps(CANARIES_ADAPTER.dump_python(tuple(canaries), mode="json"), indent=2) + "\n"


def artifacts(seed: int = SEED) -> dict[Path, str]:
    dataset, tasks, canaries = generate(seed)
    return {
        SEED_SQL: render_sql(dataset),
        TASKS_JSON: dump_tasks(tasks),
        CANARIES_JSON: render_canaries(canaries),
    }


def load_canaries(path: Path = CANARIES_JSON) -> tuple[Canary, ...]:
    return CANARIES_ADAPTER.validate_json(path.read_bytes())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testbed.seed")
    parser.add_argument("command", choices=("write", "check"))
    args = parser.parse_args(argv)

    expected = artifacts()
    if args.command == "write":
        for path, content in expected.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(REPO_ROOT)}")
        return 0

    stale = [
        path
        for path, content in expected.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    for path in stale:
        print(
            f"stale: {path.relative_to(REPO_ROOT)} (run `uv run python -m testbed.seed write`)",
            file=sys.stderr,
        )
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
