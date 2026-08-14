# LAN and Domain Deployment Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the standalone local mode safely reachable from an explicitly configured LAN Origin, and make the future Portal/domain deployment boundary executable and testable without publishing a server.

**Architecture:** Browser/API traffic remains same-origin. `serve-local` separates its bind address from one or more exact public Origins, while `Settings` supplies exact trusted hosts to the app. Production keeps a loopback default and requires an explicit trusted Host for the Portal/HTTPS reverse-proxy topology. Unauthenticated health endpoints report only process/readiness state and never probe external dependencies.

**Tech Stack:** Python 3.12, FastAPI/Starlette, Uvicorn, argparse, pytest, POSIX shell, existing React static build.

---

## File Structure

- `server/ai_creation_canvas/config.py`: validates and normalizes trusted host names separately from the existing allowed browser Origins.
- `server/ai_creation_canvas/__main__.py`: parses secure bind/public-origin/trusted-host flags; builds local settings without inferring an Origin from wildcard bind addresses.
- `server/ai_creation_canvas/app.py`: applies trusted-host middleware and provides the dependency-free health/readiness endpoints.
- `scripts/run-local.sh`: preserves the safe default and forwards optional explicit local bind/public-Origin values.
- `scripts/run-lan-local.sh`: opt-in LAN wrapper with an isolated data directory and no automatic browser launch.
- `tests/server/test_config.py`: configuration validation contract.
- `tests/server/test_cli.py`: production CLI flag and startup guard contract.
- `tests/server/test_upload_limit_cli.py`: standalone CLI wiring contract, extended with LAN arguments.
- `tests/server/test_app_security.py`: Host enforcement and health/readiness security contract.
- `tests/integration/test_local_login.py`: exact LAN Origin + CSRF integration contract.
- `README.md`, `docs/installation.md`, `docs/operations.md`, `integrations/portal/README.md`: operator-facing topology, validation, and rollback instructions.

### Task 1: Model trusted browser Origins and Hosts

**Files:**
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `tests/server/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
def test_settings_normalizes_exact_trusted_hosts_and_rejects_wildcards(tmp_path: Path) -> None:
    settings = Settings(
        "development", 8992, tmp_path / "data", "local-secret",
        identity_mode="local",
        allowed_origins=("http://192.168.1.20:8992",),
        trusted_hosts=("192.168.1.20", "canvas.local"),
    )
    assert settings.trusted_hosts == ("192.168.1.20", "canvas.local")

    with pytest.raises(ValueError, match="trusted_hosts is invalid"):
        Settings("development", 8992, tmp_path / "bad", "local-secret", trusted_hosts=("*",))


def test_origin_rejects_wildcards_and_non_origin_components(tmp_path: Path) -> None:
    for origin in ("*", "http://*.local", "http://canvas.local/path", "http://user@canvas.local"):
        with pytest.raises(ValueError, match="allowed_origins is invalid"):
            Settings("development", 8992, tmp_path / origin.replace("/", "_"), "local-secret", identity_mode="local", allowed_origins=(origin,))
```

- [ ] **Step 2: Run the focused tests and confirm red**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_config.py`

Expected: FAIL because `Settings` has no `trusted_hosts` field or accepts the new invalid values.

- [ ] **Step 3: Add the minimal `Settings.trusted_hosts` contract**

```python
trusted_hosts: tuple[str, ...] = ()

if not isinstance(self.trusted_hosts, tuple) or len(self.trusted_hosts) > 16:
    raise ValueError("trusted_hosts is invalid")
for host in self.trusted_hosts:
    if not isinstance(host, str) or not re.fullmatch(r"(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+", host) or host == "*":
        raise ValueError("trusted_hosts is invalid")
object.__setattr__(self, "trusted_hosts", tuple(dict.fromkeys(host.casefold() for host in self.trusted_hosts)))
```

Tighten the existing Origin loop so `parsed.hostname` is required and wildcard hostnames are rejected. Do not permit glob hosts, URLs, ports, paths, credentials, query strings, fragments, `null`, or dynamic Origin matching. Preserve existing empty `trusted_hosts` behavior for direct unit-test app construction; production enforcement belongs to the CLI in Task 3.

- [ ] **Step 4: Run configuration tests and verify green**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_config.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add server/ai_creation_canvas/config.py tests/server/test_config.py
git commit -m "feat: validate explicit deployment hosts"
```

### Task 2: Enforce Host boundaries and expose safe probes

**Files:**
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `tests/server/test_app_security.py`

- [ ] **Step 1: Write failing app-boundary tests**

```python
def test_trusted_host_rejects_injected_host_and_ignores_forwarded_headers(tmp_path) -> None:
    client = make_client_with_settings(
        tmp_path,
        trusted_hosts=("canvas.local",),
    )
    assert client.get("/healthz", headers={"Host": "canvas.local"}).status_code == 200
    rejected = client.get("/healthz", headers={"Host": "attacker.example", "X-Forwarded-Host": "canvas.local"})
    assert rejected.status_code == 400


def test_health_and_readiness_never_require_identity_or_leak_configuration(tmp_path) -> None:
    client = make_client(tmp_path)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert make_client_with_missing_static(tmp_path).get("/readyz").status_code == 503
```

- [ ] **Step 2: Run the focused tests and confirm red**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_app_security.py`

Expected: FAIL because the host restriction and `/healthz`/`/readyz` routes do not exist.

- [ ] **Step 3: Add minimal middleware and probe implementation**

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))

@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})

@app.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    state, _ = _static_path_state(build_dir, "index.html")
    if state is not StaticPathState.LEGIT_FILE:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready"})
```

Place the probes before the SPA catch-all and ensure the existing security response headers are applied to their responses. Do not introduce proxy-header middleware or trust any `X-Forwarded-*` input.

- [ ] **Step 4: Run app-security regression tests and verify green**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_app_security.py`

Expected: PASS, including static traversal and API identity regressions.

- [ ] **Step 5: Commit Task 2**

```bash
git add server/ai_creation_canvas/app.py tests/server/test_app_security.py
git commit -m "feat: enforce deployment hosts and probes"
```

### Task 3: Add explicit LAN and production CLI wiring

**Files:**
- Modify: `server/ai_creation_canvas/__main__.py`
- Modify: `tests/server/test_cli.py`
- Modify: `tests/server/test_upload_limit_cli.py`
- Modify: `tests/integration/test_local_login.py`

- [ ] **Step 1: Write failing CLI and LAN CSRF tests**

```python
def test_serve_local_requires_a_public_origin_when_bound_beyond_loopback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local(["--host", "0.0.0.0", "--port", "8992", "--data-dir", str(tmp_path / "data"), "--static-dir", str(tmp_path / "dist")])


def test_serve_local_wires_lan_bind_and_exact_public_origin(tmp_path, monkeypatch) -> None:
    received = {}
    monkeypatch.setattr(entrypoint, "create_local_app", lambda **kwargs: (received.update(kwargs) or SimpleNamespace(router=SimpleNamespace(on_startup=[])), None))
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda _app, **kwargs: received.update(run=kwargs))
    entrypoint._run_serve_local(["--host", "0.0.0.0", "--public-origin", "http://192.168.1.20:8992", "--port", "8992", "--data-dir", str(tmp_path / "data"), "--static-dir", str(tmp_path / "dist")])
    assert received["public_origins"] == ("http://192.168.1.20:8992",)
    assert received["run"]["host"] == "0.0.0.0"


def test_lan_origin_can_mutate_only_with_exact_csrf_origin(local_app: LocalApp) -> None:
    lan = "http://192.168.1.20:8992"
    client = make_local_client_with_origins(local_app, (lan,))
    login = client.post("/api/v1/auth/login", json={"username": local_app.accounts.user_username, "password": local_app.accounts.user_password})
    csrf = login.json()["csrf_token"]
    assert client.post("/api/v1/auth/logout", headers={"Origin": lan, "X-CSRF-Token": csrf}).status_code == 204
```

Also add a production `main()` test proving `--environment production` exits before app construction if no `--trusted-host` is supplied and passes the tuple into `Settings` when supplied.

- [ ] **Step 2: Run the focused tests and confirm red**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_cli.py tests/server/test_upload_limit_cli.py tests/integration/test_local_login.py`

Expected: FAIL because the new arguments, required startup guard, and LAN setting plumbing do not exist.

- [ ] **Step 3: Implement strict parsing and local app construction**

```python
def create_local_app(*, port: int, data_dir: Path, static_dir: Path, public_origins: tuple[str, ...] | None = None, **kwargs):
    origins = public_origins or (f"http://127.0.0.1:{port}",)
    settings = Settings(..., identity_mode="local", allowed_origins=origins, trusted_hosts=tuple(urlsplit(origin).hostname for origin in origins))

parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--public-origin", action="append", default=[])
```

Validate the bind host as IPv4 with `ipaddress.ip_address`; allow `0.0.0.0` as the wildcard and reject IPv6 for this first LAN slice because the bundled trusted-host middleware parses the `Host` header on `:`. For non-loopback bindings, require at least one public Origin; only allow `http` Origins whose hostname is a private/loopback IPv4 address or ends exactly in `.local`. Reject hostname/IP mismatch values by letting the `Settings` contract validate Origin syntax, then enforce the LAN hostname policy in the CLI. Preserve loopback default, bootstrap behavior, upload flags, and `--open` URL (`127.0.0.1`) only when the bind address is loopback.

Add repeatable `--trusted-host` to the production parser. Before `create_app`, reject production startup if it is empty. Pass it to `Settings`, retain `127.0.0.1` as the production bind default, and do not add `--public-origin` to signed Portal mode.

- [ ] **Step 4: Run focused CLI and LAN integration tests and verify green**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_cli.py tests/server/test_upload_limit_cli.py tests/integration/test_local_login.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add server/ai_creation_canvas/__main__.py tests/server/test_cli.py tests/server/test_upload_limit_cli.py tests/integration/test_local_login.py
git commit -m "feat: add explicit LAN deployment mode"
```

### Task 4: Provide safe operator entry points and deployment documentation

**Files:**
- Modify: `scripts/run-local.sh`
- Create: `scripts/run-lan-local.sh`
- Modify: `README.md`
- Modify: `docs/installation.md`
- Modify: `docs/operations.md`
- Modify: `integrations/portal/README.md`

- [ ] **Step 1: Write the failing shell/documentation checks**

```bash
sh -n scripts/run-local.sh scripts/run-lan-local.sh
rg -F 'AICC_LAN_ORIGIN is required' scripts/run-lan-local.sh
rg -F 'Portal 已登录挂载' docs/installation.md docs/operations.md
rg -F '不得暴露 Canvas 监听端口' docs/installation.md integrations/portal/README.md
```

Expected: FAIL because the LAN wrapper and required operational wording do not exist.

- [ ] **Step 2: Implement the minimal safe wrapper and document exact commands**

`scripts/run-local.sh` must continue to default `AICC_LOCAL_HOST=127.0.0.1`, omit `--public-origin` when unset, and only pass `--open` for loopback. It may pass an explicitly set `AICC_LOCAL_ORIGIN` unchanged to the validated Python CLI.

Create executable `scripts/run-lan-local.sh` with this shape:

```sh
#!/bin/sh
set -eu

: "${AICC_LAN_ORIGIN:?AICC_LAN_ORIGIN is required, for example http://192.168.1.20:8992}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AICC_LOCAL_HOST=${AICC_LOCAL_HOST:-0.0.0.0}
AICC_LOCAL_ORIGIN=$AICC_LAN_ORIGIN
AICC_LOCAL_DATA=${AICC_LOCAL_DATA:-"$(CDPATH= cd -- "$script_dir/.." && pwd)/.local-lan-data"}
export AICC_LOCAL_HOST AICC_LOCAL_ORIGIN AICC_LOCAL_DATA
exec "$script_dir/run-local.sh"
```

The documentation must show a LAN command that supplies a real private IP, use non-production test port/data, require a second-device login/change-password/demo-generation check, and state how to stop it. The public deployment section must prescribe `Internet → HTTPS proxy → Portal /ai-canvas/ → Canvas 127.0.0.1`, `--trusted-host canvas.example`, no direct public Canvas port, backend firewall, TLS/HSTS/body limit/rate limit at the public proxy, and the one-Python-replica Redis limitation. It must say that no real server or DNS has been configured by this repository change.

- [ ] **Step 3: Run shell, command-reference, and release documentation checks**

Run: `sh -n scripts/run-local.sh scripts/run-lan-local.sh && rg -F 'AICC_LAN_ORIGIN is required' scripts/run-lan-local.sh && rg -F 'Portal 已登录挂载' docs/installation.md docs/operations.md && rg -F '不得暴露 Canvas 监听端口' docs/installation.md integrations/portal/README.md && bash scripts/build-release.sh --skip-web-build`

Expected: exit 0; the final command creates and validates a temporary release directory without runtime data.

- [ ] **Step 4: Commit Task 4**

```bash
git add scripts/run-local.sh scripts/run-lan-local.sh README.md docs/installation.md docs/operations.md integrations/portal/README.md
git commit -m "docs: add LAN and Portal deployment runbook"
```

### Task 5: Full regression and isolated LAN smoke validation

**Files:**
- Modify: no source files unless a regression from the checks identifies a defect

- [ ] **Step 1: Run the complete Python suite**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q`

Expected: PASS with no failures.

- [ ] **Step 2: Build and test the frontend**

Run: `npm test --prefix web && npm run typecheck --prefix web && npm run build --prefix web`

Expected: all commands exit 0.

- [ ] **Step 3: Run release and source security gates**

Run: `bash scripts/security-scan.sh && bash scripts/build-release.sh --skip-web-build`

Expected: both commands exit 0 and the release builder reports a temporary release directory only.

- [ ] **Step 4: Run a process-level isolated LAN smoke**

Run: start `AICC_LAN_ORIGIN=http://127.0.0.1:8992 AICC_LOCAL_HOST=127.0.0.1 AICC_LOCAL_DATA="$(mktemp -d)" bash scripts/run-lan-local.sh` in a dedicated test process, then terminate that exact process after the probe requests.

Expected: process starts the offline application on test port with isolated data; use a separate test process to verify `GET /healthz`, login, and an exact-Origin CSRF mutation. Terminate only the test process; do not touch production ports or launchd.

- [ ] **Step 5: Inspect the final diff and commit any only regression fix**

Run: `git diff --check HEAD~4..HEAD && git status --short`

Expected: no whitespace errors and no runtime data, secrets, build caches, or unrelated files staged.
