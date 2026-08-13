# Real Model Smoke Report — 2026-08-13

## Scope and safety boundary

- Paid execution requires the exact opt-in `AICC_RUN_PAID_ACCEPTANCE=YES`.
- The caller must explicitly list every logical model, every channel, the Banana sample count, and `AICC_MAX_PAID_CALLS`; the total plan is rejected unless it is between 1 and 20 and within the stated budget.
- Paid credentials are accepted only from the current server process. Values are moved into a mode-0600 one-shot bundle, removed from every offline verifier environment, consumed once, and never written to this report.
- Runtime data uses a brand-new ignored `.paid-acceptance/` path or a strictly external non-symlink path. Production ports, state, logs, and processes are outside scope.
- The client emits only logical model, selected channel, status, MIME, byte count, duration, and user ID. It does not emit keys, prompts, cookies, job IDs, provider response bodies, or result URLs.

## Offline evidence

- Guard tests: PASS, including exact opt-in, explicit model/channel coverage, selected-channel key/origin checks, 1–20 hard limit, clean/offline gate ordering, ignored/external data path checks, symlink/traversal rejection, and zero-I/O guard/key-boundary probes.
- Acceptance client tests: PASS, including code-owned provider/model/route definitions, minimum-cost request shapes, all-smokes-before-batch sequencing, failed-smoke batch suppression, one-shot multi-key bundle consumption, owner/MIME/Range/download/idempotency checks, ffprobe decode, and a fake-key four-route application bootstrap with zero provider I/O.
- Full Python suite: PASS, 675 tests.
- Frontend release verification: PASS, 386 unit tests, typecheck, production build, and 7 Chromium tests.
- Production dependency audit: PASS, 0 vulnerabilities.
- Release packaging: PASS with both full-build and `--skip-web-build` paths.
- Security scan, shell/Python syntax checks, and Git diff check: PASS.

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
| Seedream | Ark | NOT RUN | — | — | — | — |
| Seedance | Ark | NOT RUN | — | — | — | — |
| Banana sample | — | NOT RUN | — | — | — | — |

Provider requests so far: **0**. The table will be updated only from sanitized client records after the tooling is committed and the same offline gates pass again from a clean worktree.

## Concerns

- Chiyun and T8Star cannot be evaluated without separately supplied server-only credentials and explicit trusted HTTPS origins; the tool fails closed rather than searching for them.
- Banana batch sampling remains disabled unless every selected representative-channel smoke succeeds.
