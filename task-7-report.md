# Task 7 report — Portal model catalog adapters

## Delivered

- Added a request-scoped `PortalClient` that only composes targets from a configured Portal base URL, an allowlisted mount, and validated relative paths.
- Added bounded timeouts and response bodies, TLS verification by default (or an explicit CA file), and disabled redirect following.
- Forwarded only the current request's validated `Cookie` header for one outbound request; it is neither stored in domain context nor reused across requests.
- Added Portal `/api/config` model mapping, strict data/schema validation, deterministic catalog results, partial-failure diagnostics, and duplicate model-ID rejection.
- Added authenticated `GET /api/v1/models` with the public `ModelSpec` JSON shape and a required inbound Portal session cookie.
- Added non-secret example declarations for image, video, and portrait-asset capabilities.

## Verification

- `.venv/bin/python -m pytest -v` — 114 passed.
- `.venv/bin/python -m compileall -q server` — passed.
- Security scan checked for disabled TLS, redirect following, credential logging, and unexpected hard-coded targets; no production-path or secret access was introduced.
- `git diff --check` — passed.

## Scope

No task submission, asset, result, or production-service work was implemented.
