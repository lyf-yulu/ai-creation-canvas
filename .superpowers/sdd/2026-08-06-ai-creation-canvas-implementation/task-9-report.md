## Exception Round 6 (user-approved, focused risk closure)

User direction was to close five concrete high-risk paths without expanding scope or
deadlocking on non-blocking details.  Verification therefore exercised production
upload/API/workflow paths, used `tmp_path` for every new test file, and did not enter
Task 10 or change progress tracking.

1. **FD cancellation (GREEN):** real `upload_with_cookie` multipart uploads block in
   `os.read`; after the first cancellation cleanup is observed waiting, followed by
   two more cancellations.  The captured FD remains open and the task remains pending
   until release, then the original `CancelledError` is raised and the FD is closed.
   The worker-`OSError` variant also keeps the original cancellation and retrieves the
   worker exception.
2. **Multipart contract (GREEN):** two requests have distinct boundaries, an exact
   `Content-Length`, exactly `group_id` and fixed-name `file` parts, and preserve data
   containing an old boundary.  CRLF MIME injection and unsupported MIME types fail
   before any request.
3. **Upstream/API mapping (RED → GREEN):** malformed JSON, non-object/missing fields,
   wrong content types, and unknown job status are rejected across group, asset, job,
   and poll paths.  The API mappings cover rejected 400, retryable 408/500, and invalid
   protocol results.  RED exposed that submit-time `InvalidUpstreamResult` became
   `UPSTREAM_UNAVAILABLE`; the minimal `jobs.py` handler now returns 502
   `UPSTREAM_INVALID` with `retryable: false` after reservation cleanup.
4. **Web recovery (GREEN):** an upload that reaches active then fails submit retains
   its asset ID; retrying with that ID does not upload again, resolves/polls and submits
   successfully.  Failed/timed-out assets never submit, and an inaccessible reused
   asset returns the typed `asset-resolve` phase.
5. **Test hygiene (GREEN):** focused tests use production paths, no fixed `/tmp`
   paths remain in the touched portrait tests, and collection increased from 215 to
   235 Python tests and from 74 to 77 web tests.

Focused verification: **24 Python + 6 web = 30 passing**.

Full verification: **235 Python + 77 web = 312 passing**; web typecheck, production
build, Python compile, security scan, and diff check also passed.  The production build
retains its pre-existing chunk-size warnings.
