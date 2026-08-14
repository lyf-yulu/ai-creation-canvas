# Admin Credential JSON Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authenticated administrator upload one strict JSON credential-pool document, replace the configured server-only pool file atomically, and immediately refresh the safe pool summaries without exposing API keys to application state or responses.

**Architecture:** Reuse `CredentialPoolLoader`, the existing admin router, CSRF middleware, and pool-summary endpoint. Add one focused importer that validates a bounded JSON upload in a temporary `0600` file before atomic replacement; add one React upload card that forwards the `File` through `FormData` without reading it. Keep provider origins, model identifiers, adapters, and operation contracts code-owned.

**Tech Stack:** FastAPI/Starlette, Pydantic, existing credential pool loader, React/TypeScript, Vitest, pytest.

---

## File structure

- Create `server/ai_creation_canvas/credential_pool_import.py`: bounded JSON-only parsing, trusted-path checks, atomic replacement, rollback and safe result type.
- Modify `server/ai_creation_canvas/credential_pools.py`: expose a JSON candidate parser that uses the same strict schema as normal loading.
- Modify `server/ai_creation_canvas/app.py`: retain the configured loader in `app.state` so import and runtime selection share one last-known-good snapshot.
- Modify `server/ai_creation_canvas/api/admin.py`: add the admin-only multipart import endpoint and safe projection.
- Create `tests/server/test_admin_credential_pool_import.py`: API authorization, CSRF, strict JSON, atomic failure and immediate success behavior.
- Modify `web/src/api/admin.ts`: add multipart client function and safe response type.
- Create `web/src/components/admin/credential-pool-import.tsx`: local file handle, explicit submit, safe status, input clearing.
- Modify `web/src/pages/admin/models.tsx`: render the import card and refresh pool summaries after success.
- Create `web/src/test/admin-credential-pool-import.test.tsx`: prove no client-side file read, single upload, failure recovery and safe display.
- Modify `README.md` and create `docs/installation.md`: portable setup and JSON example using placeholders only.
- Modify documentation tests under `tests/server/`: load the published JSON example through the real schema and check documented CLI entry points.

### Task 1: Strict JSON candidate and atomic importer

**Files:**
- Create: `server/ai_creation_canvas/credential_pool_import.py`
- Modify: `server/ai_creation_canvas/credential_pools.py`
- Test: `tests/server/test_admin_credential_pool_import.py`

- [ ] **Step 1: Write failing unit tests for the importer**

Add tests that create a configured target under `credential_pools_root`, seed it with a valid `0600` document, then assert:

```python
def test_import_replaces_valid_json_atomically_and_returns_only_safe_summary(tmp_path):
    loader, target = configured_loader(tmp_path, OLD_JSON)
    result = import_credential_pool_json(loader, target, tmp_path, NEW_JSON)
    assert result.snapshot.get("banana-chiyun").keys[0].secret == "new-secret"
    assert "secret" not in repr(result.safe_summaries)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

def test_import_failure_preserves_previous_bytes_and_snapshot(tmp_path):
    loader, target = configured_loader(tmp_path, OLD_JSON)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        import_credential_pool_json(loader, target, tmp_path, b'{"version":1,"version":1,"pools":{}}')
    assert target.read_bytes() == before
    assert loader.reload().get("old-pool") is not None
```

Also cover invalid UTF-8, more than 1 MiB, YAML syntax, duplicate JSON keys, unknown fields, empty pools, target/root symlink, unsafe existing permissions, and injected `os.replace` failure.

- [ ] **Step 2: Run the tests and verify RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_admin_credential_pool_import.py`

Expected: collection fails because `credential_pool_import` and JSON candidate parsing do not exist.

- [ ] **Step 3: Add a strict JSON parser to the existing loader module**

Implement a single shared conversion path:

```python
def parse_credential_pool_json(raw: bytes) -> CredentialPoolSnapshot:
    if len(raw) > _MAX_POOL_FILE_BYTES:
        raise _invalid_configuration()
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_unique_json_object)
        document = _CredentialPoolsInput.model_validate(payload)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise _invalid_configuration() from None
    return _snapshot_from_document(document)
```

`_unique_json_object` must raise on repeated keys. Refactor `_parse_candidate` to reuse `_snapshot_from_document` so uploaded JSON and ordinary YAML/JSON loading produce identical immutable pool objects and digests.

- [ ] **Step 4: Implement atomic import without logging secrets**

Create `import_credential_pool_json(loader, target, root, raw)` which:

1. Resolves and verifies `root` and the target parent without traversing symlinks.
2. Parses `raw` before changing the target.
3. Creates a random sibling temporary file with `os.open(..., O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)`.
4. Writes all bytes, calls `fsync`, validates the temporary file with `CredentialPoolLoader(temp, production=True).load()` and compares its digest set to the in-memory candidate.
5. Uses `os.replace(temp, target)` and `fsync` on the parent directory.
6. Calls `loader.load()` only after replacement; on any pre-replace error leaves target and loader untouched.
7. Returns `snapshot.safe_summaries()` and never includes exceptions containing input bytes.

- [ ] **Step 5: Run focused tests and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_credential_pools.py tests/server/test_admin_credential_pool_import.py`

Expected: all pass.

Commit: `feat: import credential pools atomically`

### Task 2: Admin-only upload API

**Files:**
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/api/admin.py`
- Test: `tests/server/test_admin_credential_pool_import.py`

- [ ] **Step 1: Write failing API tests**

Use the existing local-auth TestClient helpers and assert:

```python
response = admin.post(
    "/api/v1/admin/credential-pools/import",
    headers=admin_csrf_headers,
    files={"file": ("credential-pools.json", VALID_JSON, "application/json")},
)
assert response.status_code == 200
assert response.json()["pools"][0]["key_count"] == 1
assert "api_key" not in response.text
assert ordinary_user.post(...).status_code == 404
assert client.post(...without_csrf...).status_code == 403
```

Add content-type, filename, missing configured path, oversized `Content-Length`, malformed multipart and replacement-failure cases. Assert authorization rejects before consuming a streaming body.

- [ ] **Step 2: Run the API tests and verify RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_admin_credential_pool_import.py`

Expected: `POST /api/v1/admin/credential-pools/import` returns 405.

- [ ] **Step 3: Retain the loader in application state**

When managed routing is configured, set:

```python
app.state.credential_pool_loader = loader
```

When it is unavailable, set it to `None`. Keep `ManagedRoutingRuntime` using `lambda: loader.reload().as_mapping()` so a successful import is visible to the next route selection without restarting.

- [ ] **Step 4: Add the bounded multipart endpoint**

Add `POST /api/v1/admin/credential-pools/import`. Call `_require_admin(request)` before constructing the parser. Accept exactly one `.json` file with content type `application/json`, cap the full request at 1 MiB plus bounded multipart overhead, read at most 1 MiB + 1 byte, close all spool files in `finally`, then call the importer with `settings.credential_pools_path` and `settings.credential_pools_root`.

Map errors to stable, non-secret responses:

```python
raise problem(request, "CREDENTIAL_POOLS_INVALID", "The credential pool file is invalid.", status=400)
raise problem(request, "CREDENTIAL_POOLS_IMPORT_UNAVAILABLE", "Credential import is not configured.", status=409)
```

Return only `{ "pools": [...] }` from `safe_summaries()`.

- [ ] **Step 5: Run server regression and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_admin_credential_pool_import.py tests/server/test_admin_logical_models.py tests/server/test_credential_pools.py`

Expected: all pass and response bodies contain no supplied secret.

Commit: `feat: expose admin credential import`

### Task 3: Minimal admin upload card

**Files:**
- Modify: `web/src/api/admin.ts`
- Create: `web/src/components/admin/credential-pool-import.tsx`
- Modify: `web/src/pages/admin/models.tsx`
- Test: `web/src/test/admin-credential-pool-import.test.tsx`

- [ ] **Step 1: Write failing component tests**

Test that selecting a file does not submit or call `file.text`, clicking imports exactly once via `FormData`, a second click is locked while pending, the file input clears after settle, success displays only safe counts and refreshes pools, and failure leaves the existing pool cards intact.

- [ ] **Step 2: Run tests and verify RED**

Run: `npm test --prefix web -- --run src/test/admin-credential-pool-import.test.tsx`

Expected: module not found for `CredentialPoolImport`.

- [ ] **Step 3: Add the multipart API client**

```ts
export type CredentialPoolImportResult = { pools: AdminCredentialPool[] };

export const importAdminCredentialPools = (file: File) => {
    const body = new FormData();
    body.set("file", file, file.name);
    return apiFetch<CredentialPoolImportResult>("/api/v1/admin/credential-pools/import", {
        method: "POST",
        body,
    });
};
```

Do not set `Content-Type`; the browser supplies the multipart boundary and `apiFetch` supplies CSRF.

- [ ] **Step 4: Implement and integrate the upload card**

The component owns only the opaque `File` object and status. It must not call `text`, `arrayBuffer`, or `FileReader`. Provide “选择 JSON” and “导入并替换凭据池”, show filename/size only, confirm replacement explicitly, disable while uploading, clear the native input using a ref, and emit `onImported(result.pools)`.

Render it above model routing settings. On success replace `pools` with the returned safe summaries; on failure show a generic actionable message and retain the previous state.

- [ ] **Step 5: Run focused frontend checks and commit**

Run:

```bash
npm test --prefix web -- --run src/test/admin-credential-pool-import.test.tsx src/test/admin-model-routes.test.tsx src/test/admin-pages.test.tsx
npm run typecheck --prefix web
```

Expected: all pass.

Commit: `feat: upload credential JSON in admin`

### Task 4: Portable README and installation guide

**Files:**
- Modify: `README.md`
- Create: `docs/installation.md`
- Create: `server/config/credential-pools.example.json`
- Test: `tests/server/test_installation_docs.py`

- [ ] **Step 1: Write failing documentation tests**

Assert the JSON example loads through `CredentialPoolLoader`, contains only placeholder secrets, README links to the install guide, all referenced scripts exist, and CLI flags in the guide occur in `python -m ai_creation_canvas --help`.

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_installation_docs.py`

Expected: missing guide/example assertions fail.

- [ ] **Step 3: Write the portable documentation**

Document:

- Python 3.12/Node build requirements and Python-only release runtime.
- Local offline startup and first-login password change.
- External data/config directories, Redis, `AICC_CREDENTIAL_HMAC_KEY`, trusted origins, TLS/reverse proxy and backup paths.
- Admin JSON import flow and strict example schema with values such as `"api_key": "replace-with-provider-key"`; never include a real prefix/value.
- Four trusted logical models and the fact that provider protocol/model contracts stay code-owned.
- Build-release, manifest verification, upgrade/rollback and post-install checks.

- [ ] **Step 4: Run documentation tests and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_installation_docs.py tests/server/test_cli.py tests/server/test_config.py`

Expected: all pass.

Commit: `docs: add portable installation guide`

### Task 5: Proportionate release verification

**Files:**
- Modify only files required by failures caused by Tasks 1–4.

- [ ] **Step 1: Run server tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server`

Expected: all pass.

- [ ] **Step 2: Run frontend release verification**

Run: `npm run verify:release --prefix web`

Expected: unit tests, typecheck, build and Chromium checks pass; existing bundle-size warning may remain.

- [ ] **Step 3: Run security and diff checks**

Run:

```bash
scripts/security-scan.sh
npm audit --prefix web --omit=dev --audit-level=high
git diff --check
git grep -nE 'sk-|ark-[0-9a-f]{8}' -- ':!tests' ':!docs/superpowers/reports' || true
```

Expected: no secrets, no high vulnerabilities and no whitespace errors.

- [ ] **Step 4: Commit any gate-only fixes and hand off**

Commit only scoped fixes, confirm `git status --short` is empty, keep the test service separate from production ports, then start the local acceptance instance for user verification.
