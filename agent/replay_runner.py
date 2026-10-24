"""Runs a compiled replay script with Playwright and no model (ADR 0005).

Each step's element is found by role and accessible name, then by label, then by visible text, inside the
table row its `within_row` parameter identifies. The guardrails still apply: a click that pays or deletes asks
the approval callback, a verification wall stops the run for a person, and a write is verified only if the
page shows the portal's confirmation wording afterwards. Values are the task's real parameters; they go to the
portal only, never to a model.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from playwright.async_api import Locator as PageLocator
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout
from pydantic import BaseModel, ConfigDict

from agent.guardrails import CONFIRMATION, ApprovalKind, approval_for_click, is_verification_wall
from agent.replay import Locator, ReplayScript

Approve = Callable[[ApprovalKind, str], bool]
STEP_TIMEOUT_MS = 10_000
CONFIRM_TIMEOUT_MS = 5_000


class ReplayResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: str
    """success | unverified | declined | handoff | step_failed"""
    steps_done: int
    failed_step: int | None = None
    error: str | None = None
    approvals: list[dict[str, object]] = []
    seconds: float = 0.0


class StepFailed(Exception):
    pass


async def _find(page: Page, target: Locator, script: ReplayScript, params: dict[str, str]) -> PageLocator:
    scope: Page | PageLocator = page
    if target.within_row:
        scope = page.get_by_role("row").filter(has_text=params[target.within_row])
    name = script.fill(target.name, params)
    for candidate in (
        scope.get_by_role(target.role, name=name, exact=True),  # type: ignore[arg-type]
        scope.get_by_label(name, exact=True),
        scope.get_by_text(name, exact=True),
    ):
        if await candidate.count() == 1:
            return candidate
    raise StepFailed(f"no single {target.role} {target.name!r}{' in its row' if target.within_row else ''}")


async def _confirmed(page: Page) -> bool:
    """The portal shows its confirmation in a status box that renders after the write returns."""
    try:
        await page.get_by_text(CONFIRMATION).first.wait_for(state="visible", timeout=CONFIRM_TIMEOUT_MS)
    except PlaywrightTimeout:
        return False
    return True


async def _login(page: Page, origin: str, username: str, password: str) -> None:
    await page.goto(f"{origin}/login")
    await page.locator("input:not([type=password]):not([type=hidden])").first.fill(username)
    secret = page.locator("input[type=password]").first
    await secret.fill(password)
    await secret.press("Enter")
    await page.wait_for_load_state("networkidle")


async def run_script(
    script: ReplayScript,
    params: dict[str, str],
    origin: str,
    username: str,
    password: str,
    approve: Approve,
    executable_path: str | None = None,
) -> ReplayResult:
    started = time.perf_counter()
    approvals: list[dict[str, object]] = []
    done = 0

    def result(outcome: str, **extra: object) -> ReplayResult:
        return ReplayResult(
            outcome=outcome,
            steps_done=done,
            approvals=approvals,
            seconds=round(time.perf_counter() - started, 2),
            **extra,
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=executable_path)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(STEP_TIMEOUT_MS)
        try:
            await _login(page, origin, username, password)
            for index, step in enumerate(script.steps):
                if is_verification_wall(await page.locator("body").aria_snapshot()):
                    return result("handoff", failed_step=index, error="verification wall")
                try:
                    if step.action == "navigate":
                        await page.goto(origin + script.fill(str(step.url), params))
                    elif step.action == "press":
                        await page.keyboard.press(str(step.value))
                    else:
                        assert step.target is not None
                        element = await _find(page, step.target, script, params)
                        if step.action == "click":
                            name = script.fill(step.target.name, params)
                            kind = approval_for_click(step.target.role, name, quarantined=False)
                            if kind is not None:
                                approved = approve(kind, name)
                                approvals.append({"step": index, "kind": kind.value, "approved": approved})
                                if not approved:
                                    return result("declined", failed_step=index)
                            await element.click()
                        elif step.action == "type":
                            await element.fill(script.fill(str(step.value), params))
                            if step.submit:
                                await element.press("Enter")
                        else:
                            await element.select_option(script.fill(str(step.value), params))
                    await page.wait_for_load_state("networkidle")
                except (StepFailed, PlaywrightTimeout) as e:
                    return result("step_failed", failed_step=index, error=str(e)[:300])
                except Exception as e:  # Playwright's own errors (detached element, navigation) end the run
                    return result("step_failed", failed_step=index, error=f"{type(e).__name__}: {e}"[:300])
                done = index + 1
            return result("success" if await _confirmed(page) else "unverified")
        finally:
            await browser.close()
