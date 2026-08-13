# Real Model Smoke Report — 2026-08-13

## Scope and safety boundary

- Paid execution requires the exact opt-in `AICC_RUN_PAID_ACCEPTANCE=YES`.
- The caller must explicitly list every logical model, every channel, the Banana sample count, and `AICC_MAX_PAID_CALLS`. A process-local atomic counter is consumed before every provider submission, including every route/key fallback; it fails closed at the configured ceiling and can never exceed 20.
- Paid credentials are accepted only from the current server process. Values are moved into a mode-0600 one-shot bundle, removed together with every locator from offline verifier environments, consumed once, and never written to this report. Cleanup unlinks only files whose trusted parent and file inode still match the creator's recorded identity.
- Runtime data is restricted to one brand-new direct child of this repository's ignored `.paid-acceptance/` root and is created through no-follow directory descriptors. Arbitrary external paths and the production project are rejected.
- Ark uses its fixed code-owned origin. Chiyun and T8Star have no approved exact origin in this task, so caller-supplied HTTPS URLs cannot enable those channels.
- The client emits only logical model, selected channel, status, safe failure/not-run class, MIME, byte count, duration, and user ID. One run recorder encloses upload and every other preflight, route activation, and execution step. With intact failure-recording state, it attempts the detailed summary pipeline exactly once. If failure recording has already failed, it skips a second attempt and writes the fixed fallback directly. If summary construction, rendering, or output fails, it makes a best-effort write of one fixed non-sensitive `failed` fallback record to an independent stderr sink; failure of either or both reporting sinks never replaces the original run exception. It does not claim that a detailed summary succeeded when its output failed, and it does not emit keys, prompts, cookies, job IDs, provider response bodies, error text, or result URLs.

## Offline evidence

- Guard tests: PASS, including exact opt-in, explicit model/channel coverage, selected-channel key/origin checks, 1–20 hard limit, clean/offline gate ordering, ignored/external data path checks, symlink/traversal rejection, and zero-I/O guard/key-boundary probes.
- Acceptance client tests: PASS, including four-model contract coverage, minimum-cost request shapes, all-smokes-before-batch sequencing, failed-smoke batch suppression, one-shot multi-key bundle consumption, owner/MIME/Range/download/idempotency checks, ffprobe decode, and a fake-key two-route Ark application bootstrap with zero provider I/O. Four-model contract coverage is not a claim that four paid routes were configured or run.
- The Seedream and Seedance acceptance declarations are cross-checked directly against `server/config/ark-models.example.json` and accepted by the managed route factory. The administrator's route-contract projection preserves the exact Ark schemas without injecting an internal profile marker. Seedance 2.5 uses the formal five ports, 30-image/10-audio limits, six parameters, and 480p/720p resolution set. Seedream specializes the formal multi-operation declaration to `image.edit` by tightening the reference minimum to one; it does not weaken that boundary or add text-to-image to this acceptance.
- Banana and GPT-Image2 contracts are cross-checked against the server factory's fixed Chiyun schema and the administrator's trusted templates. Because Chiyun/T8Star have no approved origins, those contracts are offline coverage only and their routes are NOT RUN.
- Full Python suite: PASS, 697 tests.
- Frontend release verification: PASS, 388 unit tests, typecheck, production build, and 7 Chromium tests.
- Production dependency audit: PASS, 0 vulnerabilities.
- Release packaging: PASS with both full-build and `--skip-web-build` paths.
- Security scan, shell/Python syntax checks, and Git diff check: PASS.
- Review-fix focused gates: PASS, 185 server/acceptance tests plus 16 frontend model-contract, route-template, and graph-compile tests. Explicit 5xx responses, including Ark video 5xx bodies containing a task-like ID, are retryable; business 4xx and ambiguous transport/protocol outcomes remain non-replayable.
- With a working output sink, summary regression coverage proves upload failure, owner/assignment/visibility preflight failure, route activation failure, and an unexpected execution exception each produce exactly one redacted summary. Every planned smoke or batch entry is classified as succeeded, failed, or not run; exceptions are not swallowed.
- Reporting-failure regression coverage forces summary construction, summary rendering, primary output, `fail_current`, and both primary/fallback outputs to fail. The original run exception object remains authoritative in every case; the fixed fallback contains only `phase`, `status=failed`, and `failure_class=summary_pipeline_failed`.
- Round-3 focused gates: PASS, 190 server/acceptance tests, including 5/5 reporting-pipeline fault regressions.
- Post-fix full offline gates: PASS. No provider request was made during any of the three fix rounds; another real run remains prohibited until independent review marks the harness ready.

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

The job-owner isolation check completed before the failed terminal state. MIME, decode, download, result-owner isolation, and idempotency replay could not be verified because no result was produced; the acceptance therefore did not pass. This run used the review-rejected pre-fix harness and is retained only as historical evidence. No additional real call was made during any fix round.

## Concerns

- Chiyun and T8Star cannot be evaluated until exact origins are approved in trusted code as well as server-only credentials being supplied; arbitrary HTTPS input is not an approval mechanism.
- The Ark Seedream submission ended in an uncertain state. The retained redacted state cannot distinguish a provider 5xx, a response/transport ambiguity, or another fail-closed submission condition, so an automatic retry would risk a duplicate paid request.
- Seedance was not run after the Seedream failure, and Banana batch sampling remained disabled because the representative-channel smokes did not all succeed.
- Seedream `image.edit` deliberately requires at least one reference image. The frontend template was corrected to match the server instead of weakening server validation; text-to-image remains a separate `image.generate` contract outside this fix.
