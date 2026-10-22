"""Scripted oracles: plain Playwright, no LLM, proving that each task is solvable through the UI per scenario.

Oracles know the chaos config, so they pick the label vocabulary the SPA is rendering and dismiss the
interstitial. They locate by role and accessible name, except on the poor-accessibility form, where they fall
back to structural CSS that an accessibility-tree agent cannot see.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.async_api import Page, expect

from testbed.scenarios import ChaosConfig
from testbed.tasks import Intent, Task

VARIANTS_JSON = Path(__file__).resolve().parent.parent / "portal" / "web" / "src" / "chaos" / "variants.json"
REIMBURSEMENT_FIELDS = ("memberNumber", "claimNumber", "amount", "payeeName", "bankRouting", "bankAccount")


class VerificationWallError(Exception):
    """The portal demanded human verification at sign-in."""


class Labels:
    def __init__(self, chaos: ChaosConfig) -> None:
        variants = json.loads(VARIANTS_JSON.read_text(encoding="utf-8"))
        self._labels: dict[str, str] = variants["drift" if chaos.dom_drift.labels else "normal"]

    def __call__(self, key: str) -> str:
        return self._labels[key]


class Oracle:
    def __init__(self, page: Page, base_url: str, chaos: ChaosConfig) -> None:
        self.page = page
        self.base_url = base_url
        self.chaos = chaos
        self.t = Labels(chaos)

    async def sign_in(self, username: str, password: str) -> None:
        page, t = self.page, self.t
        await page.goto(f"{self.base_url}/login")
        await page.get_by_label(t("login.username"), exact=True).fill(username)
        await page.get_by_label(t("login.password"), exact=True).fill(password)
        await page.get_by_role("button", name=t("login.submit"), exact=True).click()
        wall = page.get_by_role("heading", name="Security check")
        signed_in = page.get_by_role("navigation", name="Main")
        await expect(wall.or_(signed_in)).to_be_visible()
        if await wall.is_visible():
            raise VerificationWallError(username)

    async def run(self, task: Task) -> str | None:
        """Performs the task. Returns the answer for read intents, None for writes."""
        match task.intent:
            case Intent.UPDATE_ADDRESS:
                await self._update_address(task)
            case Intent.UPDATE_CONTACT:
                await self._update_contact(task)
            case Intent.CLAIM_STATUS:
                return await self._claim_status(task)
            case Intent.POLICY_LOOKUP:
                return await self._policy_lookup(task)
            case Intent.SUBMIT_REIMBURSEMENT:
                await self._submit_reimbursement(task)
            case Intent.ADD_NOTE:
                await self._add_note(task)
            case Intent.REQUEST_RECONSIDERATION:
                await self._request_reconsideration(task)
            case Intent.DELETE_NOTE:
                await self._delete_note(task)
        return None

    async def _open_member(self, member_number: str) -> None:
        page = self.page
        await page.goto(f"{self.base_url}/members/{member_number}")
        if self.chaos.dom_drift.interstitial:
            dialog = page.get_by_role("dialog", name="Portal update")
            await dialog.get_by_role("button", name=self.t("interstitial.continue"), exact=True).click()
            await expect(dialog).to_be_hidden()
        await expect(page.get_by_role("heading", level=1)).to_contain_text(self.t("member.heading"))

    async def _saved(self, text: str) -> None:
        await expect(self.page.get_by_role("status").filter(has_text=text)).to_be_visible()

    async def _update_address(self, task: Task) -> None:
        page, t, p = self.page, self.t, task.params
        await self._open_member(task.member_number)
        await page.get_by_role("button", name=t("address.edit"), exact=True).click()
        await page.get_by_label(t("address.street"), exact=True).fill(p["street"])
        await page.get_by_label(t("address.city"), exact=True).fill(p["city"])
        await page.get_by_label(t("address.state"), exact=True).fill(p["state"])
        await page.get_by_label(t("address.postalCode"), exact=True).fill(p["postal_code"])
        await page.get_by_role("button", name=t("address.save"), exact=True).click()
        await self._saved("Address saved.")

    async def _update_contact(self, task: Task) -> None:
        page, t = self.page, self.t
        await self._open_member(task.member_number)
        await page.get_by_role("button", name=t("contact.edit"), exact=True).click()
        if "phone" in task.params:
            await page.get_by_label(t("contact.phone"), exact=True).fill(task.params["phone"])
        if "email" in task.params:
            await page.get_by_label(t("contact.email"), exact=True).fill(task.params["email"])
        await page.get_by_role("button", name=t("contact.save"), exact=True).click()
        await self._saved("Contact details saved.")

    async def _claim_status(self, task: Task) -> str:
        page = self.page
        await page.goto(f"{self.base_url}/claims/{task.params['claim_number']}")
        label = self.t("claim.status")
        value = page.locator(f"xpath=//dl/dt[normalize-space()='{label}']/following-sibling::dd[1]")
        return (await value.inner_text()).strip()

    async def _policy_lookup(self, task: Task) -> str:
        page = self.page
        await self._open_member(task.member_number)
        table = page.get_by_role("table", name=self.t("policies.heading"), exact=True)
        row = table.get_by_role("row").filter(
            has=page.get_by_role("cell", name=task.params["product"], exact=True)
        )
        return (await row.get_by_role("cell").first.inner_text()).strip()

    async def _submit_reimbursement(self, task: Task) -> None:
        page, t, p = self.page, self.t, task.params
        await page.goto(
            f"{self.base_url}/reimbursements/new?member={task.member_number}&claim={p['claim_number']}"
        )
        values = {
            "memberNumber": task.member_number,
            "claimNumber": p["claim_number"],
            "amount": p["amount"],
            "payeeName": p["payee_name"],
            "bankRouting": p["bank_routing"],
            "bankAccount": p["bank_account"],
        }
        if self.chaos.poor_a11y:
            inputs = page.locator(".pa-form input.pa-input")
            await expect(inputs).to_have_count(len(REIMBURSEMENT_FIELDS))
            for index, key in enumerate(REIMBURSEMENT_FIELDS):
                await inputs.nth(index).fill(values[key])
            await page.locator("canvas.pa-button").first.click()
            async with page.expect_response(re.compile(r"/internal/reimbursements$")) as response:
                await page.locator("canvas.pa-button").nth(1).click()
            if (await response.value).status != 201:
                raise AssertionError(f"reimbursement rejected: {await (await response.value).text()}")
            return
        labels = {
            "memberNumber": "reimb.memberNumber",
            "claimNumber": "reimb.claimNumber",
            "amount": "reimb.amount",
            "payeeName": "reimb.payee",
            "bankRouting": "reimb.routing",
            "bankAccount": "reimb.account",
        }
        for key in REIMBURSEMENT_FIELDS:
            await page.get_by_label(t(labels[key]), exact=True).fill(values[key])
        await page.get_by_role("button", name=t("reimb.review"), exact=True).click()
        await page.get_by_role("button", name=t("reimb.confirm"), exact=True).click()
        await self._saved("Reimbursement submitted.")

    async def _add_note(self, task: Task) -> None:
        page, t = self.page, self.t
        await self._open_member(task.member_number)
        await page.get_by_label(t("notes.new"), exact=True).fill(task.params["body"])
        await page.get_by_role("button", name=t("notes.add"), exact=True).click()
        await self._saved("Note added.")

    async def _request_reconsideration(self, task: Task) -> None:
        page = self.page
        await page.goto(f"{self.base_url}/claims/{task.params['claim_number']}")
        await page.get_by_role("button", name=self.t("claim.reconsider"), exact=True).click()
        await self._saved("Reconsideration requested.")

    async def _delete_note(self, task: Task) -> None:
        page, t = self.page, self.t
        await self._open_member(task.member_number)
        table = page.get_by_role("table", name=t("notes.heading"), exact=True)
        row = table.get_by_role("row").filter(
            has=page.get_by_role("cell", name=task.params["note_body"], exact=True)
        )
        await row.get_by_role("button", name=t("notes.delete"), exact=True).click()
        dialog = page.get_by_role("dialog", name="Delete note?")
        await dialog.get_by_role("button", name=t("notes.confirmDelete"), exact=True).click()
        await self._saved("Note deleted.")
