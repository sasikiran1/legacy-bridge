# 0010. Portal frontend: React single-page app over internal endpoints

- Status: Accepted
- Date: 2026-10-21

## Context

The spec called for server-rendered HTML (Thymeleaf or JSP) to make the portal feel legacy. Sasi chose a modern frontend instead. The design has to keep the property the whole project depends on: when the public API is degraded, the task is still possible through the UI. Otherwise the browser tier has nothing to fall back to.

## Decision

**The portal is a half-modernized legacy system:** a React single-page app (SPA) on top of the Spring Boot backend.

**Frontend stack:**
- Vite 8 + React 19 + TypeScript 7, in `portal/web/`.
- React Router 8 in SPA (declarative) mode.
- Plain `fetch` with no data-fetching library, and no component library.

**Build and serving:**
- A multi-stage Docker build: `node:26.9.0` builds the bundle; the Maven stage copies it into Spring Boot's static resources.
- One container and one origin; session-cookie auth unchanged.
- Spring forwards unknown non-API paths to `index.html`.

**Two API surfaces:**
- **Public API** `/api/v1/**`
  - documented by springdoc
  - the only API the agent's API tier may call
  - this is where the chaos toggles remove, deprecate, and drift endpoints
- **Internal API** `/internal/**`
  - the SPA's own JSON endpoints
  - excluded from the OpenAPI document and absent from the agent's API-tier allowlist
  - left working when the public API is degraded

  The agent only reaches it indirectly, by driving the UI in the browser tier.

**Look:** dated intranet styling in plain CSS (dense tables, grey bevels, small fonts). No component library, because libraries generate clean accessibility trees and polished UI, which would work against the drift and poor-accessibility chaos.

**Chaos in the SPA:** the app reads the active chaos config from `/internal/chaos` on load.
- DOM drift: a variant map renames IDs and labels and moves buttons.
- An interstitial-modal component.
- The poor-accessibility page uses a `<canvas>` and click-handling `<div>`s with no roles or labels.
- Slow responses delay the internal endpoints, which exercises the agent's wait strategy.

## Alternatives

- **Thymeleaf (original spec).** The most believable legacy, and the simplest. Declined in favour of a modern frontend.
- **Thymeleaf + htmx.** A middle ground with partial page updates. Declined for the same reason.
- **Next.js.** Server rendering needs a Node server next to Spring, which means two backends to run and secure. Its server-side data layer also blurs which API is "public".
- **Angular.** Workable, but heavier than needed for ~10 screens.

## Consequences

- **The fallback story becomes the strangler-fig pattern:** a new UI on internal endpoints, a stale and partial public API, and an agent that can only use the public one. That's a common real enterprise setup and easy to explain in an interview.
- **Pages render after JavaScript runs.** Replay scripts and the oracles must wait on locators, not page loads. ADR 0005 has to account for this.
- **The project gains a Node toolchain.** CI adds an `npm ci && npm run build` step, and `package-lock.json` is committed.
- **The browser tier sees the same data flowing through `/internal/**` in network traffic.** The executor's domain allowlist covers the portal origin. The API-tier tool generator reads only the springdoc document, so internal endpoints never become tools.
- **Frontend tests:** the Playwright oracles cover end-to-end behaviour. A unit-test runner (e.g. Vitest) would be a separate dependency request in M1 if the chaos-variant logic needs it.
