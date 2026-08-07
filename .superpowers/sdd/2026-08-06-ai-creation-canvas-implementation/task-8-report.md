# Task 8 execution report

Implemented owned local reference assets, metadata-only SQLite job persistence,
idempotent generation submission, owned job polling, and protected result proxy
routes. The SQLite schema excludes prompts, request parameters, credentials,
media bodies, and upstream result URLs; it stores a canonical request hash and
an opaque result reference only.

Added tests covering idempotent reuse/conflict and ownership, upload magic-byte
and actual-size enforcement with temporary-file cleanup, and protected single
range result responses.

Verification completed:

- Round-one RED/GREEN: storage tests first exposed missing WAL/lease behavior;
  implementation then made those tests pass. The final focused storage suite
  collected 2 tests successfully.
- `PYTHONPATH=server:. .venv/bin/pytest -q` — 160 passed
- `python3 -m compileall -q server`
- `./scripts/security-scan.sh`
- `git diff --check`

## Round 5 final hardening

- Result streams now close for every pre-header validation failure, reject
  non-identity content encoding, use raw wire bytes for length accounting,
  return structured 502 for faulty upstream responses, and reject `bytes=-0`.
- Portal submission maps 4xx/5xx and transport failures to typed errors while
  preserving cancellation.  Non-retryable submission failures become terminal;
  retryable failures release the lease for recovery.
- Model selection resolves local adapters before protected adapters, so a local
  model can run without a cookie while protected/unknown selection returns 401
  without reserving a job.  Poll authentication no longer disappears into the
  transient-error branch.
- Legacy result migration now scrubs opaque IDs in Python with the shared
  strict identifier expression and enables SQLite secure deletion.

Final verification:

- `PYTHONPATH=server:. .venv/bin/pytest -q` — 185 passed
- `python3 -m compileall -q server`
- `./scripts/security-scan.sh`
- `git diff --check`

## Round 4 recovery checklist (exception continuation)

- A. Result streaming: added a failing `Content-Range` contract test (initial
  collection failure for the missing validator), then validate 206 start/end,
  total size, and declared body length before headers are sent.  Focused
  range, HEAD, malformed-range, and first-chunk close tests are green.
- B. Provider idempotency: preserved the inherited `GenerationPort` contract
  clarification and added exact Portal POST payload coverage, including
  `idempotency_key`; real async concurrent same-user/key submission has one
  canvas job and one fake upstream create.
- C. Response methods and bounds: `create_app` continues to allow Portal
  GET/POST/HEAD.  Result tests exercise an actual upstream HEAD, local 416
  without a forged `Content-Range`, and a first chunk larger than the declared
  length (structured 502 plus provider close).
- D. Store leases and migration: preserved and ran stale-token CAS, active
  lease, reopened-store recovery, WAL, and legacy `result_ref` rebuild tests.
- E. Assets: added streamed truncated-PNG rejection, no temporary artifact,
  `Path.read_bytes` prohibition, cross-user 403, and owner-only file mode
  coverage.  Uploads now retain a bounded tail for PNG/JPEG framing and verify
  WebP RIFF length without buffering the media body.
- F. Polling and result ownership: existing transient-poll, monotonic terminal
  state, opaque result identifier, and owner checks remain green; the new
  206 test proves mismatched provider ranges cannot be proxied.
- G. Cookie boundary: a cookie-required adapter now receives 401 before model
  catalog invocation and before any `canvas_jobs` row is reserved; local
  adapters remain usable without a cookie in the existing generation flow.
- H. Submission failures: existing typed retryable/non-retryable submission
  mapping remains covered by the focused generation suite; no failure branch
  returns 201.
- I. Evidence: RED observed for the missing `validate_partial_response`, the
  truncated PNG acceptance (201 rather than 415), and late cookie validation
  (catalog call count 1 rather than 0).  Their minimal fixes are green.

Round 4 verification at that point:

- `PYTHONPATH=server:. .venv/bin/pytest -q` — 181 passed
- `python3 -m compileall -q server`
- `./scripts/security-scan.sh`
- `git diff --check`
