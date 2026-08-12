# Governed Model Registry and Chiyun Verification Report

## Outcome

The repository now has a FastAPI/SQLite governed model registry with administrator-owned Provider, model and access objects. React consumes safe capability projections; credentials remain deployment-owned. The first allowlisted dynamic adapter is Chiyun GPT Image 2 for `image.edit` only. Redis-backed execution permits provide cross-process concurrency bounds while SQLite remains authoritative for authorization, audit, immutable submissions and idempotency.

## Verified behavior

- Provider/model definitions are versioned, audited and operation-specific.
- Ordinary users see only assigned, enabled and credential-healthy models.
- Public model JSON excludes base URL, credential reference, provider model name and parameter mappings.
- Chiyun uses one exact `POST /v1/images/edits` with ordered multipart `image[]` parts and server-resolved Bearer authentication.
- Concurrent equal idempotency keys reserve one SQL job and make one provider request; changed payloads conflict.
- Jobs retain immutable model/provider/adapter/submission snapshots.
- Results are owner-scoped and available through bounded same-origin GET, HEAD and Range responses.
- Revocation blocks new submissions before the provider boundary.
- Redis keys contain hashed opaque identifiers only and releases occur on success, failure and cancellation.
- The canvas palette is capability-driven: an account assigned only an `image.edit` model can create a usable image model node instead of being blocked by an `image.generate`-only UI check.

## Fresh gates

- Python: `435 passed in 55.87s`.
- Web JSDOM: `35 files / 361 tests passed`.
- TypeScript: passed.
- Production web build: passed.
- Chromium: `1 file / 3 tests passed`.
- Production npm audit: `0 vulnerabilities`.
- Isolated Chiyun registry integration: `1 passed`.
- Signal-cleanup probe: passed five consecutive runs after atomic probe publication was added.
- Browser acceptance on `127.0.0.1:9002`: ordinary-user login, canvas creation and visible GPT Image 2 edit-node capability/parameters/ports passed with zero console errors.

Parallel combined gates were intentionally discarded: the server release tests run `npm ci`, so overlapping them with frontend verification temporarily removes Vite/TypeScript from that same worktree. A separate acceptance signal probe also exposed a real empty-file publication race. The probe now publishes atomically; final server and frontend gates were rerun sequentially and are green.

## Boundaries and deferred work

- No real Chiyun credential was read and no paid provider call was made.
- The offline acceptance transport returns a fixed bounded PNG and is not evidence of provider availability, billing or regional connectivity.
- Redis currently coordinates execution permits; it is not yet a durable Redis Streams background worker queue. Multi-replica crash takeover remains production hardening.
- The Chiyun slice accepts bounded `b64_json` PNG output. HTTPS URL result download remains fail-closed until a separate SSRF-safe downloader policy is implemented and verified.
- Existing Vite output still reports the known large main chunk and mixed static/dynamic import warning; build success is unaffected, but code splitting remains performance work.
