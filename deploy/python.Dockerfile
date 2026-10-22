# Python services and tooling: the PII proxy, lint/type checks, tests, the Playwright oracle suite, analysis.
# The Playwright base image carries Chromium and its system libraries; uv installs Python 3.14 and the lockfile
# (CPU-only torch). Source is bind-mounted at /repo by compose; model weights live in the hf-cache volume.
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

COPY --from=astral/uv:0.12.9 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    HF_HOME=/cache/hf

WORKDIR /repo
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --no-install-project
