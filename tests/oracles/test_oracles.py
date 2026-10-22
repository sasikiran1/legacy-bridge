"""Validates the testbed: every task is solvable through the UI under every scenario that permits it.

docker compose up -d && uv run pytest -m oracle [--scenarios healthy,dom_drift]
"""

from __future__ import annotations

import pytest
from playwright.async_api import Browser

from testbed.oracles import Oracle, VerificationWallError
from testbed.portal import PortalAdmin
from testbed.postconditions import check, snapshot
from testbed.scenarios import Scenario, load_scenarios
from testbed.settings import Settings
from testbed.tasks import Task, load_tasks

pytestmark = pytest.mark.oracle

SCENARIOS = load_scenarios()
TASKS = load_tasks()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "scenario" in metafunc.fixturenames:
        selected = metafunc.config.getoption("--scenarios")
        names = list(SCENARIOS) if selected == "all" else selected.split(",")
        metafunc.parametrize("scenario", [SCENARIOS[n] for n in names], ids=names)


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
async def test_oracle_solves_task(
    scenario: Scenario, task: Task, admin: PortalAdmin, browser: Browser, portal_settings: Settings
) -> None:
    await admin.apply(scenario.chaos)
    await admin.reset()
    before = await snapshot(portal_settings.portal_db_url)

    context = await browser.new_context(viewport={"width": 1024, "height": 768})
    page = await context.new_page()
    page.set_default_timeout(10_000)
    oracle = Oracle(page, portal_settings.portal_base_url, scenario.chaos)
    try:
        if scenario.ui_blocked:
            with pytest.raises(VerificationWallError):
                await oracle.sign_in(task.user, portal_settings.portal_test_user_password)
            return
        await oracle.sign_in(task.user, portal_settings.portal_test_user_password)
        answer = await oracle.run(task)
    finally:
        await context.close()

    result = check(task.expected, before, await snapshot(portal_settings.portal_db_url), answer)
    assert result.passed, f"missing={result.missing} unexpected={result.unexpected}"
