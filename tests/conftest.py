from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--scenarios",
        default="all",
        help="comma-separated scenario names for the oracle suite (default: all)",
    )
    parser.addoption("--headed", action="store_true", help="show the browser while oracles run")
