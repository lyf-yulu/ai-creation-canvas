# Task 10 report

- Canvas project generation now uses only same-origin `/api/v1/jobs` submission and job polling.
- Successful jobs append one result node using `sourceJobId`; failed jobs append a scoped failure node with prompt, parameters, local asset IDs, idempotency key, request ID, and phase.
- Pending references persist per storage scope. A response-lost submission remains dormant after refresh and a matching manual retry reuses its idempotency key. Multiple resumed jobs have independent poll controllers.
- The canvas reads `/api/v1/models`, filters by declared operations, sends the selected `model_id`, and exposes the safe parameter-schema controls. Retry is explicit from a failed node token; identical new submissions receive a new key.
- Verification: `npm test --prefix web` (88 passed), `npm run typecheck --prefix web`, `npm run build --prefix web`, `bash scripts/security-scan.sh`, and `git diff --check`.
