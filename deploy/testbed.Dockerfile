# Python toolchain + Chromium for lint, type checks, unit tests and the Playwright oracle suite.
# The Playwright base image carries the browser and its system libraries; uv installs Python 3.14 and the lockfile.
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

COPY --from=astral/uv:0.12.9 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_FROZEN=1

WORKDIR /repo
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --no-install-project
