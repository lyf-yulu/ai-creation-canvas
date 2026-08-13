# Real Model Smoke Report — 2026-08-13

## Scope and safety boundary

- Paid execution requires the exact opt-in `AICC_RUN_PAID_ACCEPTANCE=YES`.
- The caller must explicitly list every logical model, every channel, the Banana sample count, and `AICC_MAX_PAID_CALLS`. A process-local atomic counter is consumed before every provider submission, including every route/key fallback; it fails closed at the configured ceiling and can never exceed 20.
- Paid credentials are accepted only from the current server process. Values are moved into a mode-0600 one-shot bundle, removed together with every locator from offline verifier environments, consumed once, and never written to this report. Cleanup unlinks only files whose trusted parent and file inode still match the creator's recorded identity.
- Runtime data is restricted to one brand-new direct child of this repository's ignored `.paid-acceptance/` root and is created through no-follow directory descriptors. Arbitrary external paths and the production project are rejected.
- Ark uses its fixed code-owned origin. Chiyun and T8Star have no approved exact origin in this task, so caller-supplied HTTPS URLs cannot enable those channels.
- The client emits only logical model, selected channel, status, MIME, byte count, duration, and user ID. It does not emit keys, prompts, cookies, job IDs, provider response bodies, or result URLs.

## Offline evidence

- Guard tests: PASS, including exact opt-in, explicit model/channel coverage, selected-channel key/origin checks, 1–20 hard limit, clean/offline gate ordering, ignored/external data path checks, symlink/traversal rejection, and zero-I/O guard/key-boundary probes.
- Acceptance client tests: PASS, including code-owned provider/model/route definitions, minimum-cost request shapes, all-smokes-before-batch sequencing, failed-smoke batch suppression, one-shot multi-key bundle consumption, owner/MIME/Range/download/idempotency checks, ffprobe decode, and a fake-key four-route application bootstrap with zero provider I/O.
- Full Python suite: PASS, 686 tests.
- Frontend release verification: PASS, 388 unit tests, typecheck, production build, and 7 Chromium tests.
- Production dependency audit: PASS, 0 vulnerabilities.
- Release packaging: PASS with both full-build and `--skip-web-build` paths.
- Security scan, shell/Python syntax checks, and Git diff check: PASS.
- Review-fix focused gates: PASS, 159 server/acceptance tests plus 9 frontend model-contract and graph-compile tests. Explicit 5xx responses, including Ark video 5xx bodies containing a task-like ID, are retryable; business 4xx and ambiguous transport/protocol outcomes remain non-replayable.
- Post-fix full offline gates: PASS. No provider request was made during the fix round; another real run remains prohibited until independent review marks the harness ready.

## Controlled inputs

| Input | State |
| --- | --- |
| Chiyun server-only key | UNSET |
| T8Star server-only key | UNSET |
| Ark server-only key | SET |

Only explicitly selected channels with a SET key may run. Chiyun and T8Star are therefore NOT RUN in this acceptance.

## Paid results

| Logical model | Selected channel | Status | MIME | Bytes | Duration | User ID |
| --- | --- | --- | --- | ---: | ---: | --- |
| Seedream | Ark (`seedream-ark`) | FAILED (`submission_unknown`; the superseded client emitted the coarser `acceptance_contract`) | — | — | — | `Mibhz7fGDQaPuNH60-oaslSt` |
| Seedance | Ark | NOT RUN | — | — | — | — |
| Banana sample | — | NOT RUN | — | — | — | — |

Paid smoke attempts: **1 of 2**; successes: **0**; failures: **1**; not run: **1**. The isolated store recorded exactly one Seedream job with the expected user, route, and a present idempotency value. It stopped at `submission_unknown` on attempt 1 and produced no result row. The uncertain submission was not retried, so Seedance and every batch call remained unexecuted.

The job-owner isolation check completed before the failed terminal state. MIME, decode, download, result-owner isolation, and idempotency replay could not be verified because no result was produced; the acceptance therefore did not pass. This run used the review-rejected pre-fix harness and is retained only as historical evidence. No additional real call was made during the fix round.

## Concerns

- Chiyun and T8Star cannot be evaluated until exact origins are approved in trusted code as well as server-only credentials being supplied; arbitrary HTTPS input is not an approval mechanism.
- The Ark Seedream submission ended in an uncertain state. The retained redacted state cannot distinguish a provider 5xx, a response/transport ambiguity, or another fail-closed submission condition, so an automatic retry would risk a duplicate paid request.
- Seedance was not run after the Seedream failure, and Banana batch sampling remained disabled because the representative-channel smokes did not all succeed.
- Seedream `image.edit` deliberately requires at least one reference image. The frontend template was corrected to match the server instead of weakening server validation; text-to-image remains a separate `image.generate` contract outside this fix.
