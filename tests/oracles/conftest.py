from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Browser, async_playwright

from testbed.portal import PortalAdmin
from testbed.settings import Settings, settings


@pytest.fixture(scope="session")
def portal_settings() -> Settings:
    return settings()


@pytest.fixture(scope="session")
async def admin(portal_settings: Settings) -> AsyncIterator[PortalAdmin]:
    async with PortalAdmin(portal_settings) as client:
        await client.wait_ready()
        yield client


@pytest.fixture(scope="session")
async def browser(request: pytest.FixtureRequest) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        instance = await playwright.chromium.launch(headless=not request.config.getoption("--headed"))
        yield instance
        await instance.close()
