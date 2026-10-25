# Python services and tooling: the PII proxy, the agent, lint/type checks, tests, oracles, analysis.
# The Playwright base image carries Chromium and its system libraries; uv installs Python 3.14 and the lockfile
# (CPU-only torch). Node and @playwright/mcp come from the official Node image so the agent can drive the browser
# over MCP. Source is bind-mounted at /repo by compose; model weights live in the hf-cache volume.
FROM node:26.9.0-slim AS mcp
RUN npm install -g @playwright/mcp@0.0.83 --no-audit --no-fund

FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

COPY --from=astral/uv:0.12.9 /uv /uvx /bin/
COPY --from=mcp /usr/local/bin/node /usr/local/bin/node
COPY --from=mcp /usr/local/lib/node_modules/@playwright/mcp /usr/local/lib/node_modules/@playwright/mcp
# Tesseract OCR for screenshot redaction in the proxy (ADR 0008).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr=5.3.4-1build5 tesseract-ocr-eng=1:4.1.0-2 \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --version | head -1
RUN ln -s /usr/local/lib/node_modules/@playwright/mcp/cli.js /usr/local/bin/playwright-mcp \
    && node --version

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    HF_HOME=/cache/hf

WORKDIR /repo
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --no-install-project
