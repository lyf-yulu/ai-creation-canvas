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
- Final trusted-routing review wave: PASS with zero provider I/O. The legacy Provider POST/PUT APIs now return a read-only rejection, while Provider GET omits Base URL and credential reference. Historical untrusted providers survive restart only as inert audit data and do not enter the protocol map. A server-owned registry fixes four logical profiles and five exact channel presets; compatible legacy route bodies remain accepted only when provider ID, provider model name, adapter, family, and the entire operation contract match one preset exactly. Historical non-preset routes are excluded before adapter construction. Route enable reloads the current model, provider, pool, and route, and a missing/incompatible pool produces no route mutation or success audit. The admin UI also prevents enabling an existing disabled route after its compatible pool disappears.
- Final-wave TDD evidence: the initial server test failed because no trusted registry existed, and the initial UI regression showed the enable control remained active after pool removal. The final focused suites pass 22/22 server boundary tests and 8/8 frontend calling-setting tests. The full Python server/contract/integration suite passes 702 tests; the full frontend suite passes 389 tests; typecheck, production build, 7 Chromium tests, production dependency audit, both release-package paths, and the security scan pass.
- Post-fix full offline gates: PASS. No provider request was made during any of the three fix rounds. After independent review marked the harness ready, one new Seedream-only Ark smoke was authorized and run with a process-local provider submission budget of exactly one.
- The first execution attempt stopped before data-directory/server creation or provider I/O because the default temporary directory traversed a symlink rejected by the release-packaging gate. After confirming zero provider submissions, the same one-call plan was rerun with the verified real system root `/private/tmp`; this preflight-only stop is not counted as a provider call.

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
| Seedream | Ark (`seedream-ark`) | SUCCEEDED | `image/png` | 289,954 | 20.62 s | `P1aE39JOWepGuJuvfeGAURFL` |
| Seedance | Ark | NOT RUN | — | — | — | — |
| Banana sample | — | NOT RUN | — | — | — | — |

Newly authorized paid smoke attempts: **1 of 1**; successes: **1**; failures: **0**; not run in this plan: **0**. The plan selected only Seedream Ark, set `AICC_MAX_PAID_CALLS=1`, and set the Banana sample count to zero. Seedance, Chiyun, T8Star, GPT-Image2, and all batch work were outside the plan and NOT RUN.

The successful path verified asset/job/result ownership, stored user/route/idempotency assignment, `image/png` consistency, local decode, HEAD, a nonempty 206 Range response, bounded full download, and replay of the identical idempotency key to the same succeeded job. The replay consumed no additional provider submission; the one-call process budget would have failed closed before any second provider POST. The isolated database contains exactly one succeeded job at attempt 1 with no error.

The canonical canvas project was not present before or during provider submission. After provider success and process shutdown, a pure-local app with no managed routing or provider credentials was opened on the same isolated data directory. The smoke owner then created and read back a project containing prompt, reference, model, job, and result nodes with three canonical graph connections; an administrator read returned 404. This closure made no `/jobs` request and left the database at exactly one job/attempt 1.

Historical note: the earlier review-rejected pre-fix Seedream attempt remains separately classified `submission_unknown`, with no result, and was never restored or retried. Its retained evidence is not counted as part of this newly authorized 1/1 smoke.

## Concerns

- Chiyun and T8Star cannot be evaluated until exact origins are approved in trusted code as well as server-only credentials being supplied; arbitrary HTTPS input is not an approval mechanism.
- The historical Ark Seedream submission remains uncertain and must never be restored or automatically retried; the successful smoke used a new account, job, idempotency key, and isolated data path.
- The process-local provider counter ended with the acceptance process; its live value is not persisted. The run evidence proves one selected/attempted/succeeded logical call under a hard maximum of one, while the isolated database independently proves one job at attempt 1.
- Seedance and Banana batch sampling were intentionally not selected for the newly authorized smoke.
- Seedream `image.edit` deliberately requires at least one reference image. The frontend template was corrected to match the server instead of weakening server validation; text-to-image remains a separate `image.generate` contract outside this fix.
