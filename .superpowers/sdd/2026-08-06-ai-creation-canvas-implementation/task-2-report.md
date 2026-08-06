# Task 2 report

## Implemented

- Added the Vitest gate and a red/green test for `SECURITY_POLICY`.
- Added a credential-free same-origin `/api/v1/` client plus session, models, assets, and jobs contracts.
- Replaced image, video, and audio provider calls with task-compatible same-origin adapters.
- Removed browser API-key, arbitrary backend URL, remote-plugin, dynamic-script, prompt-source, and WebDAV configuration entry points.
- Moved `CanvasNodeDefinition` to the local controlled node-types module and removed plugin runtime registration.
- Added `scripts/security-scan.sh` and a reproducible npm peer-dependency compatibility setting in `web/.npmrc`.

## Verification

- `npm ci --prefix web`: passed (clean install).
- `npm test --prefix web`: passed, 1 test.
- `npm run typecheck --prefix web`: passed.
- `npm run build --prefix web`: passed. Vite reported only existing chunk-size/dynamic-import optimization warnings.
- `bash scripts/security-scan.sh`: passed.
- `git diff --check`: passed.

## Note

The clean install's npm audit output reports 20 inherited dependency advisories (3 low, 7 moderate, 10 high). The Task 2 required source security scan passes; resolving those transitive advisories needs a separately reviewed dependency upgrade because the upstream snapshot contains an Ant Design peer-version conflict. `web/.npmrc` fixes that installation conflict reproducibly with `legacy-peer-deps=true`.

## Review fix round 1

### Red evidence

Before the fix, `npm test --prefix web -- --run src/test/api-security.test.ts src/test/jobs.test.ts src/test/reference-assets.test.ts` had 8 expected failures: dot-segment and encoded traversal reached `fetch`, `assetUrl`, `waitForJob`, and `assetIdsForReferences` did not exist.

### Disposition

- `apiFetch` now rejects absolute, protocol-relative, non-API, raw/encoded/doubly-encoded dot segment, and encoded separator paths before calling `fetch`; it always uses same-origin credentials.
- Results use `asset_id` and produce only checked `/api/v1/assets/{id}` addresses. Provider URLs are no longer consumed by image/video adapters.
- `waitForJob` is a shared bounded poller (default 1 second / 120 seconds) with injectable fetch and sleep functions; queued/running jobs now wait through terminal success, failure, cancellation, or timeout.
- Reference handling now accepts only explicit `asset_id` values. Legacy local data/URL references throw an upload-required error rather than being submitted as an empty list.
- Restored `src/**/*.ts(x)` type checking by deleting unreachable legacy code that depended on removed browser configuration/plugin surfaces.
- Confirmed `axios` has no production references and removed it from the package manifest/lockfile. npm audit subsequently reports 18 inherited advisories (3 low, 7 moderate, 8 high), down from 20; remaining advisories require separate upstream dependency review.
- Expanded the source scan into documented multi-pattern tripwires; behavior is covered by the new Vitest tests.

Verification after the fix: clean `npm ci` passed; `npm test` passed (4 files, 13 tests); full `npm run typecheck` with `src/**/*.ts(x)` passed; `npm run build`, `bash scripts/security-scan.sh`, and `git diff --check` passed. The implementation commit is `eeeba42f992cdb197dde807ad07aa4032a20666d`.
