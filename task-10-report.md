# Task 10 report

- Canvas project generation now uses only same-origin `/api/v1/jobs` submission and job polling.
- Successful jobs append one result node using `sourceJobId`; failed jobs append a scoped failure node with prompt, parameters, local asset IDs, idempotency key, request ID, and phase.
- Pending references persist per storage scope. A response-lost submission remains dormant after refresh and a matching manual retry reuses its idempotency key. Multiple resumed jobs have independent poll controllers.
- The canvas reads `/api/v1/models`, filters by declared operations, sends the selected `model_id`, and exposes the safe parameter-schema controls. Retry is explicit from a failed node token; identical new submissions receive a new key.
- Standard object-schema `properties`/`required` input is handled as a data-only subset with defaults, finite range and enum validation. Restore reads are isolated by storage-scope version.
- Enum controls preserve their original string or numeric schema values through DOM selection; invalid or mixed enum declarations are ignored safely.
- Primitive defaults are accepted only when type, finiteness, integer and range constraints hold; job parameters are rebuilt from validated controls at submit time.
- Missing parameter values are represented only by `undefined`; valid empty strings, `false`, and `0` are retained, while cleared numeric input is omitted rather than coerced to zero.
- Defaults are applied only during model initialization; clearing a field keeps its rendered state empty rather than restoring the schema default.
- Verification: `npm test --prefix web` (88 passed), `npm run typecheck --prefix web`, `npm run build --prefix web`, `bash scripts/security-scan.sh`, and `git diff --check`.
