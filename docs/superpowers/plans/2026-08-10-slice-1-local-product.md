# Slice 1 Local Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可一键启动和打开的真实黑绿 Web UI，让一个管理员和一个普通用户完成登录、模型派发、项目持久化及无付费图片任务回填，并证明普通用户之间的数据隔离。

**Architecture:** 保留现有 Infinite Canvas、同源 `/api/v1`、Python 控制平面、SQLite、适配器注册表和任务恢复逻辑。新增本地 Cookie 身份档、服务端项目存储、管理员模型派发和完全离线的 Demo 生成适配器；Portal 签名身份档继续保留。前端使用受保护路由和产品外壳组织真实页面，浏览器不获得密钥，云端基础设施不在本切片实现。

**Tech Stack:** Python 3.12、FastAPI、SQLite、PBKDF2-HMAC-SHA256、React 19、TypeScript、Zustand、React Router、Ant Design、Tailwind CSS、Vitest、pytest。

## Global Constraints

- 产品设计规格：`docs/superpowers/specs/2026-08-10-ai-creation-canvas-product-design.md`，状态为已获用户书面批准。
- Infinite Canvas 固定基线：`9bccd0ff1a7057a835708a731644ab05371fea3b`；许可证 AGPL-3.0。
- 本切片只做真实 UI、双角色身份、模型派发、项目持久化和离线 Demo 图片任务；不接真实 Key、不产生付费请求。
- 普通用户只看到管理员派发的模型；浏览器不能读取 API Key、Base URL、secret_ref、远程插件或动态脚本。
- 本地测试端口使用 `8992`，不得连接、停止或重启 `9090`、`8787`、`8797`、`8891`、`8991`，不得操作 launchd。
- 测试数据仅写入临时目录或被 Git 忽略的 `.local-data/`；不得读取原 Portal 的生产密钥、状态、数据、输出或日志。
- 手工交付账号只有一个管理员和一个普通用户；自动化隔离测试额外创建两个临时普通用户夹具。
- Cookie 会话为 HttpOnly；CSRF token 只保存在前端内存；退出和用户切换必须停止旧作用域请求并清理内存状态。
- 黑绿主题中正文不用纯荧光绿；底部任务托盘必须有安全高度，iframe、窄屏和 200% 缩放不得遮挡正文。
- 每个任务按 TDD 完成，先确认红灯，再写最小实现；每个任务通过自己的定向门禁后独立提交。

## File Structure

### Python

- `server/ai_creation_canvas/auth/passwords.py`：PBKDF2 密码编码和常量时间验证。
- `server/ai_creation_canvas/auth/local.py`：本地用户、会话、CSRF 与一次性初始密码业务规则。
- `server/ai_creation_canvas/adapters/demo.py`：无网络、无密钥的图片 Demo 模型和受控结果流。
- `server/ai_creation_canvas/api/auth.py`：登录、退出和改初始密码。
- `server/ai_creation_canvas/api/admin.py`：用户列表、启停与模型派发。
- `server/ai_creation_canvas/api/projects.py`：按 owner 隔离的项目 CRUD 和版本冲突。
- `server/ai_creation_canvas/api/activity.py`：当前用户资产和任务列表，用于真实资产/任务页面。
- `server/ai_creation_canvas/catalog.py`：在现有 `ModelCatalog` 外执行用户模型派发过滤。
- `server/ai_creation_canvas/storage/sqlite.py`：本切片的用户、会话、模型派发、项目及列表查询表和原子方法。
- `server/ai_creation_canvas/app.py`：装配本地或 Portal 身份档、CSRF、路由和 Demo 适配器。
- `server/ai_creation_canvas/config.py`：身份档、Cookie、会话时长和 Demo 开关配置。
- `server/ai_creation_canvas/__main__.py`：初始化账号、服务启动和 `--open`。

### Web

- `web/src/api/auth.ts`、`admin.ts`、`projects.ts`、`activity.ts`：同源 API 客户端。
- `web/src/components/auth/auth-gate.tsx`：会话装载、未登录跳转和角色保护。
- `web/src/components/layout/product-shell.tsx`：黑绿产品导航、主内容安全区和账号动作。
- `web/src/components/layout/task-tray.tsx`：底部任务托盘，不遮挡主内容。
- `web/src/components/canvas/generation-inspector.tsx`：能力驱动的模型、提示词和参数面板。
- `web/src/components/canvas/generation-node-card.tsx`：来源、进行中、失败和结果节点视图。
- `web/src/features/projects/project-sync.ts`：服务端权威项目与作用域安全的保存/恢复。
- `web/src/pages/auth/login.tsx`：登录与首次改密页面。
- `web/src/pages/admin/users.tsx`、`models.tsx`：真实用户和模型派发页面。
- `web/src/pages/tasks/index.tsx`：真实任务列表。
- `web/src/pages/canvas/index.tsx`、`project.tsx`、`web/src/pages/assets/index.tsx`：接入产品外壳与服务端数据。
- `web/src/router.tsx`、`styles/globals.css`、`lib/app-theme.ts`：受保护路由和黑绿主题。

---

### Task 1: Local Identity Kernel and Bootstrap Accounts

**Files:**
- Create: `server/ai_creation_canvas/auth/__init__.py`
- Create: `server/ai_creation_canvas/auth/passwords.py`
- Create: `server/ai_creation_canvas/auth/local.py`
- Create: `server/ai_creation_canvas/api/auth.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/api/session.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/__main__.py`
- Test: `tests/server/test_local_auth.py`
- Test: `tests/integration/test_local_login.py`

**Interfaces:**
- Produces: `PasswordHasher.hash(password: str) -> str` and `PasswordHasher.verify(password: str, encoded: str) -> bool`.
- Produces: `LocalAuthService.create_user(username, display_name, password, role, must_change_password) -> PortalUser`, `bootstrap_accounts(initial_user_model_ids: tuple[str, ...] = ()) -> BootstrapResult`, `login(username, password) -> IssuedSession`, `resolve(session_token) -> PortalUser | None`, `verify_csrf(session_token, csrf_token) -> bool`, `logout(session_token) -> None`, `change_initial_password(user_id, current_password, new_password) -> None`.
- Produces: `Settings.identity_mode: Literal["signed_portal", "local"]`, `session_ttl_seconds`, `session_cookie_name`, `allowed_origins: tuple[str, ...]`, `enable_demo_adapter`.
- Preserves: signed Portal identity behavior and existing API contract tests.

- [ ] **Step 1: Write password and session red tests**

```python
def test_password_hash_is_salted_and_verifies():
    first = PasswordHasher.hash("correct-horse-battery")
    second = PasswordHasher.hash("correct-horse-battery")
    assert first != second
    assert PasswordHasher.verify("correct-horse-battery", first)
    assert not PasswordHasher.verify("wrong-password-000", first)

def test_local_session_is_hashed_at_rest_and_expires(tmp_path):
    store = CanvasStore(tmp_path)
    auth = LocalAuthService(store, session_ttl_seconds=60, clock=lambda: 1000)
    auth.create_user("user-a", "普通用户", "correct-horse-battery", PortalRole.USER, must_change_password=True)
    issued = auth.login("user-a", "correct-horse-battery")
    assert issued.session_token not in store.database.read_bytes().decode("latin1")
    resolved = auth.resolve(issued.session_token)
    assert resolved is not None
    assert resolved.user_id == "user-a"
```

- [ ] **Step 2: Run the unit tests and confirm red**

Run: `pytest -q tests/server/test_local_auth.py`

Expected: collection fails because `ai_creation_canvas.auth` does not exist.

- [ ] **Step 3: Add exact password and local-auth primitives**

```python
class PasswordHasher:
    ALGORITHM = "pbkdf2_sha256"
    ITERATIONS = 310_000

    @classmethod
    def hash(cls, password: str) -> str:
        cls._validate(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, cls.ITERATIONS)
        return f"{cls.ALGORITHM}${cls.ITERATIONS}${salt.hex()}${digest.hex()}"

    @classmethod
    def verify(cls, password: str, encoded: str) -> bool:
        try:
            algorithm, rounds, salt, expected = encoded.split("$", 3)
            if algorithm != cls.ALGORITHM or int(rounds) != cls.ITERATIONS:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
            return hmac.compare_digest(actual, bytes.fromhex(expected))
        except (TypeError, ValueError):
            return False
```

Passwords accept 12–128 Unicode characters. `LocalAuthService` normalizes usernames with `strip().casefold()`, stores only password hashes and the SHA-256 hash of each 32-byte random session token, enforces disabled users and expiry, and rotates the session on login and password change. CSRF token 本身可存于会话表，因为刷新页面后同源 `GET /session` 需要重新取得它；它不能单独用于认证，比较时仍使用 `hmac.compare_digest`。

- [ ] **Step 4: Add additive SQLite schema and typed store methods**

Create `canvas_users`, `canvas_sessions`, and `canvas_user_models` in `_migrate_schema()`:

```sql
CREATE TABLE IF NOT EXISTS canvas_users (
  user_id TEXT PRIMARY KEY,
  username_normalized TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','user')),
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  must_change_password INTEGER NOT NULL CHECK(must_change_password IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_sessions (
  token_hash TEXT PRIMARY KEY,
  csrf_token TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES canvas_users(user_id) ON DELETE CASCADE,
  expires_at REAL NOT NULL,
  created_at TEXT NOT NULL
);
```

Store methods must use fixed SQL projections and return copied dictionaries: `create_user`, `user_by_username`, `user_by_id`, `update_user_password`, `set_user_enabled`, `create_session`, `session_user`, `delete_session`, `purge_expired_sessions`.

- [ ] **Step 5: Add local identity middleware and auth endpoints**

```python
class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)

@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response):
    issued = request.app.state.local_auth.login(body.username, body.password)
    response.set_cookie(
        request.app.state.settings.session_cookie_name,
        issued.session_token,
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        path="/",
        max_age=request.app.state.settings.session_ttl_seconds,
    )
    return {"user": session_payload(issued.user), "csrf_token": issued.csrf_token}
```

`POST /api/v1/auth/logout` requires the valid in-memory CSRF header, deletes the server session, expires the Cookie and returns `204`. `POST /api/v1/auth/change-password` rotates the session and returns the replacement CSRF token. `GET /api/v1/session` returns `{user_id, username, role, must_change_password, csrf_token}` only in local mode; signed Portal mode keeps its current fields and never invents a CSRF token.

The application middleware must skip authentication only for `POST /api/v1/auth/login`, select local Cookie or signed Portal headers by `Settings.identity_mode`, reject invalid/expired sessions with the existing safe `AUTH_REQUIRED`, and preserve current request IDs and security headers.

For local-mode `POST`, `PUT`, `PATCH` and `DELETE`, require both a valid CSRF token and an `Origin` that exactly matches `Settings.allowed_origins`; missing, `null`, wildcard, lookalike and alternate-port origins return 403. The local launcher supplies only `http://127.0.0.1:8992` for the default port. Signed Portal mode keeps its separately configured trusted origin behavior.

- [ ] **Step 6: Add bootstrap command behavior**

```python
@dataclass(frozen=True, slots=True)
class BootstrapResult:
    admin_username: str
    admin_password: str
    user_username: str
    user_password: str
    created: bool
```

`bootstrap_accounts()` atomically creates `canvas-admin` and `canvas-user` only when the user table is empty, uses separate `secrets.token_urlsafe(18)` passwords, sets `must_change_password=1`, and returns plaintext only in the newly-created result. `__main__.py init-local` prints the two credentials once to the terminal; an existing database prints only “accounts already initialized”.

- [ ] **Step 7: Write and run API integration tests**

```python
def test_login_cookie_session_csrf_logout_and_disabled_user(local_app):
    login = local_app.post("/api/v1/auth/login", json={"username": "canvas-user", "password": local_app.user_password})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    csrf = login.json()["csrf_token"]
    assert local_app.get("/api/v1/session").json()["role"] == "user"
    assert local_app.post("/api/v1/auth/logout").status_code == 403
    headers = {"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:8992"}
    assert local_app.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert local_app.get("/api/v1/session").status_code == 401
```

Also assert login failures use the same public message for unknown user and wrong password, expired sessions fail, password change rotates Cookie/CSRF, wrong or missing Origin fails, local mode never accepts signed identity headers, and signed Portal mode still passes its existing tests.

Run: `pytest -q tests/server/test_local_auth.py tests/integration/test_local_login.py tests/server/test_identity.py tests/server/test_app_security.py`

Expected: all pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add server/ai_creation_canvas/auth server/ai_creation_canvas/api/auth.py server/ai_creation_canvas/api/session.py server/ai_creation_canvas/storage/sqlite.py server/ai_creation_canvas/config.py server/ai_creation_canvas/app.py server/ai_creation_canvas/__main__.py tests/server/test_local_auth.py tests/integration/test_local_login.py
git commit -m "feat: add standalone local identity"
```

### Task 2: Secure Frontend Login and Session Scope

**Files:**
- Create: `web/src/api/auth.ts`
- Create: `web/src/components/auth/auth-gate.tsx`
- Create: `web/src/pages/auth/login.tsx`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/api/contracts.ts`
- Modify: `web/src/stores/portal/use-session-store.ts`
- Modify: `web/src/router.tsx`
- Test: `web/src/test/auth-flow.test.tsx`
- Test: `web/src/test/api-client.test.ts`
- Test: `web/src/test/session-store.test.ts`

**Interfaces:**
- Consumes: `POST /api/v1/auth/login`, `logout`, `change-password`, `GET /api/v1/session` from Task 1.
- Produces: `setCsrfToken(value: string | null): void`; `login(username, password): Promise<PortalSession>`; `logout(): Promise<void>`; `AuthGate` and `RoleGate`.
- Preserves: existing scope lease behavior and generation cancellation on account change.

- [ ] **Step 1: Add red tests for CSRF memory and route gating**

```tsx
it("adds the in-memory csrf token only to same-origin mutations", async () => {
  setCsrfToken("csrf-1");
  await apiFetch("/api/v1/projects", { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledWith("/api/v1/projects", expect.objectContaining({
    headers: expect.objectContaining({ "X-CSRF-Token": "csrf-1" }),
  }));
});

it("redirects an unauthenticated browser to login", async () => {
  render(<AuthGate><div>private</div></AuthGate>);
  expect(await screen.findByRole("heading", { name: "登录 AI 创作画布" })).toBeVisible();
  expect(screen.queryByText("private")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and confirm red**

Run: `npm test --prefix web -- --run src/test/auth-flow.test.tsx src/test/api-client.test.ts src/test/session-store.test.ts`

Expected: fail because auth client and gates do not exist.

- [ ] **Step 3: Implement the in-memory CSRF client**

```ts
let csrfToken: string | null = null;
export function setCsrfToken(value: string | null) { csrfToken = value; }

const method = (init.method ?? "GET").toUpperCase();
const mutation = !["GET", "HEAD", "OPTIONS"].includes(method);
const headers = new Headers(init.headers);
headers.set("Accept", "application/json");
if (mutation && csrfToken) headers.set("X-CSRF-Token", csrfToken);
```

Keep `safeApiPath`, same-origin credentials and safe error parsing unchanged. Never place the CSRF token in localStorage, Zustand persistence, URL parameters or logs. Login is the only mutation permitted without an existing token.

- [ ] **Step 4: Implement session store actions and gates**

Extend `PortalSession` with optional `must_change_password` and never include `csrf_token` in the persisted session type. `login()` sets the returned CSRF token in module memory, then calls the existing lease-safe `activateSession`. `logout()` calls the API, clears the token in `finally`, aborts old scope listeners and clears user memory. `AuthGate` renders a full-page loading state, the login page on 401, the password-change form when required, and children otherwise. `RoleGate allowed={["admin"]}` shows a safe 404-style panel for a normal user.

- [ ] **Step 5: Verify account-switch race and password-change behavior**

Add tests where user A session resolution is delayed, user B logs in, then A resolves; assert B remains active and A cannot rehydrate or write B’s canvas. Assert logout clears CSRF even when the logout response fails. Assert password change replaces the old token and returns to the requested route.

Run: `npm test --prefix web -- --run src/test/auth-flow.test.tsx src/test/api-client.test.ts src/test/session-store.test.ts src/test/canvas-scope-race.test.ts`

Expected: all pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add web/src/api/auth.ts web/src/api/client.ts web/src/api/contracts.ts web/src/components/auth/auth-gate.tsx web/src/pages/auth/login.tsx web/src/stores/portal/use-session-store.ts web/src/router.tsx web/src/test/auth-flow.test.tsx web/src/test/api-client.test.ts web/src/test/session-store.test.ts
git commit -m "feat: add secure local login UI"
```

### Task 3: Black-Green Product Shell and Real Activity Pages

**Files:**
- Create: `server/ai_creation_canvas/api/activity.py`
- Create: `web/src/api/activity.ts`
- Create: `web/src/components/layout/product-shell.tsx`
- Create: `web/src/components/layout/task-tray.tsx`
- Create: `web/src/pages/tasks/index.tsx`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `web/src/pages/assets/index.tsx`
- Modify: `web/src/layouts/user-layout.tsx`
- Modify: `web/src/router.tsx`
- Modify: `web/src/styles/globals.css`
- Modify: `web/src/lib/app-theme.ts`
- Test: `tests/server/test_activity_api.py`
- Test: `web/src/test/product-shell.test.tsx`

**Interfaces:**
- Produces: `GET /api/v1/activity/assets` and `GET /api/v1/activity/jobs`, each returning only the current owner’s newest 100 metadata rows.
- Produces: `ProductShell`, `TaskTray`, `/projects`, `/assets`, `/tasks`, and role-aware `/admin/*` navigation slots.
- Does not expose unreleased History, Skill, Usage or credential routes; future navigation items remain in a typed registry with `released: false` and are not rendered.

- [ ] **Step 1: Write owner-list and layout red tests**

```python
def test_activity_lists_only_current_owner(app_as):
    app_as.store.create_asset(asset_id="a-owned", user_id="user-a", kind="reference", mime_type="image/png", relative_path="assets/a.png", size_bytes=12)
    app_as.store.create_asset(asset_id="b-hidden", user_id="user-b", kind="reference", mime_type="image/png", relative_path="assets/b.png", size_bytes=12)
    assert [item["asset_id"] for item in app_as.user_a.get("/api/v1/activity/assets").json()["assets"]] == ["a-owned"]
```

```tsx
it("keeps the bottom tray outside the scrollable content", () => {
  render(<ProductShell><div>content</div></ProductShell>);
  expect(screen.getByTestId("product-main")).toHaveClass("pb-[var(--task-tray-height)]");
  expect(screen.getByTestId("task-tray")).toHaveClass("fixed", "bottom-0");
});
```

- [ ] **Step 2: Run red tests**

Run: `pytest -q tests/server/test_activity_api.py && npm test --prefix web -- --run src/test/product-shell.test.tsx`

Expected: first command fails because the endpoint is missing; after adding only the server test, the web test still fails because the shell is missing.

- [ ] **Step 3: Add bounded owner list queries and API**

```python
def list_assets_for_owner(self, user_id: str, limit: int = 100) -> tuple[dict[str, object], ...]:
    safe_limit = min(max(limit, 1), 100)
    with self._connection() as db:
        rows = db.execute(
            "SELECT asset_id,kind,mime_type,status,size_bytes,created_at,updated_at "
            "FROM canvas_assets WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, safe_limit),
        ).fetchall()
    return tuple(dict(row) for row in rows)
```

Add `list_jobs_for_owner()` with this fixed projection: `id, service_id, operation, status, error_code, created_at, updated_at`, ordered by `created_at DESC` and capped at 100. The API obtains the user only through `context_for(request)` and never accepts `user_id` query parameters. Cross-user IDs do not appear in counts or diagnostics.

- [ ] **Step 4: Apply exact black-green tokens**

Set dark mode as the product default and define semantic variables:

```css
:root, .dark {
  --background: oklch(0.085 0.012 145);
  --foreground: oklch(0.94 0.025 145);
  --card: oklch(0.12 0.018 145);
  --card-foreground: oklch(0.94 0.025 145);
  --primary: oklch(0.82 0.20 145);
  --primary-foreground: oklch(0.10 0.025 145);
  --muted: oklch(0.17 0.025 145);
  --muted-foreground: oklch(0.72 0.035 145);
  --border: oklch(0.30 0.055 145);
  --ring: oklch(0.82 0.20 145);
  --task-tray-height: 3.5rem;
}
```

Ant Design maps primary, focus, selected and success tokens to the same semantic palette. Warning and error remain amber/red with text labels. Add `.media-surface` and `.embed-surface` with opaque card backgrounds and visible borders so transparent media and same-origin iframe content remain readable.

- [ ] **Step 5: Build the real shell, asset page and task page**

`ProductShell` renders only released routes, a user/role badge, logout, mobile navigation and `<main data-testid="product-main">`. `TaskTray` initially derives active jobs from `useGenerationJob` task registry added in Task 7; until then it renders a truthful “暂无运行任务” state. Assets and tasks pages call their APIs and show loading, actual rows, safe error state or an empty state; they do not use fake counts.

- [ ] **Step 6: Verify accessibility and route behavior**

Tests must assert normal users never receive an admin link, admin users do, keyboard focus has a visible class, the shell does not render unreleased links, media surfaces have a non-transparent background class, and the task tray is not nested inside the main scroll container.

Run: `pytest -q tests/server/test_activity_api.py && npm test --prefix web -- --run src/test/product-shell.test.tsx && npm run typecheck --prefix web`

Expected: all pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add server/ai_creation_canvas/api/activity.py server/ai_creation_canvas/storage/sqlite.py server/ai_creation_canvas/app.py web/src/api/activity.ts web/src/components/layout/product-shell.tsx web/src/components/layout/task-tray.tsx web/src/pages/tasks/index.tsx web/src/pages/assets/index.tsx web/src/layouts/user-layout.tsx web/src/router.tsx web/src/styles/globals.css web/src/lib/app-theme.ts tests/server/test_activity_api.py web/src/test/product-shell.test.tsx
git commit -m "feat: add black green product shell"
```

### Task 4: Admin User and Model Assignment

**Files:**
- Create: `server/ai_creation_canvas/catalog.py`
- Create: `server/ai_creation_canvas/api/admin.py`
- Create: `web/src/api/admin.ts`
- Create: `web/src/pages/admin/users.tsx`
- Create: `web/src/pages/admin/models.tsx`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/api/models.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `web/src/router.tsx`
- Test: `tests/server/test_admin_api.py`
- Test: `tests/server/test_model_assignments.py`
- Test: `web/src/test/admin-pages.test.tsx`

**Interfaces:**
- Produces: runtime-checkable `ModelCatalogPort`, plus `AssignedModelCatalog.list_models(context, cookie_header=None) -> CatalogResult` and `resolve_model(context, model_id, cookie_header=None) -> ModelSpec`.
- Produces: `GET /api/v1/admin/users`, `PATCH /api/v1/admin/users/{user_id}`, `GET /api/v1/admin/models`, `PUT /api/v1/admin/users/{user_id}/models`.
- Consumes: local roles from Task 1 and the real shell from Task 3.

- [ ] **Step 1: Write red tests for direct-ID bypass and admin authorization**

```python
async def test_unassigned_model_is_hidden_and_cannot_be_submitted(local_app):
    local_app.assign("user-a", ["demo-image-v1"])
    models = local_app.user_a.get("/api/v1/models").json()["models"]
    assert [item["model_id"] for item in models] == ["demo-image-v1"]
    response = local_app.user_a.post("/api/v1/jobs", headers=local_app.user_a.csrf, json=job_payload(model_id="hidden-model"))
    assert response.status_code == 400
    assert response.json()["code"] == "MODEL_UNAVAILABLE"

def test_normal_user_cannot_call_admin_api(local_app):
    response = local_app.user_a.get("/api/v1/admin/users")
    assert response.status_code == 404
```

- [ ] **Step 2: Run red tests**

Run: `pytest -q tests/server/test_admin_api.py tests/server/test_model_assignments.py`

Expected: fail because assignment catalog and admin API do not exist.

- [ ] **Step 3: Add assignment storage and catalog decorator**

Use `canvas_user_models(user_id, model_id, created_at, PRIMARY KEY(user_id, model_id))`. `replace_model_assignments(user_id, model_ids)` uses one immediate transaction, caps the set at 128 stable IDs and rejects disabled/unknown users. `ModelCatalogPort` declares the two async catalog methods and is implemented by both the existing `ModelCatalog` and `AssignedModelCatalog`; `api/models.py` validates this internal protocol instead of requiring the concrete `ModelCatalog` class. `AssignedModelCatalog` asks the wrapped catalog for safe model declarations, then filters by the current user’s assignments; admins receive all models. `resolve_model` first checks assignment and only then calls the wrapped resolver, preventing direct model-ID bypass.

- [ ] **Step 4: Add admin API with concealed authorization failures**

```python
def require_admin(request: Request) -> PortalUser:
    user = context_for(request).user
    if user.role is not PortalRole.ADMIN:
        raise problem(request, "API_NOT_FOUND", "The requested API resource was not found.", status=404)
    return user
```

User list returns user ID, display name, role, enabled, must-change-password and assigned model IDs, never password hashes or session records. The PATCH body permits only `{enabled: bool}` in Slice 1. Model assignment body is strict `{model_ids: list[str]}`. All mutations use Task 1 CSRF middleware.

- [ ] **Step 5: Add actual admin pages**

The users page lists both bootstrap accounts, supports enable/disable with a confirmation, and displays no password or reset placeholder. The models page lists real catalog rows and checkboxes for the selected user; save uses one PUT and refetches. Normal-user routes are blocked by `RoleGate` before rendering and still rejected server-side.

- [ ] **Step 6: Run server and web tests**

Run: `pytest -q tests/server/test_admin_api.py tests/server/test_model_assignments.py tests/server/test_models_api.py && npm test --prefix web -- --run src/test/admin-pages.test.tsx src/test/model-picker.test.ts && npm run typecheck --prefix web`

Expected: all pass; tests assert no response contains `password_hash`, session token, CSRF hash, secret or provider URL.

- [ ] **Step 7: Commit Task 4**

```bash
git add server/ai_creation_canvas/catalog.py server/ai_creation_canvas/api/admin.py server/ai_creation_canvas/storage/sqlite.py server/ai_creation_canvas/api/models.py server/ai_creation_canvas/api/jobs.py server/ai_creation_canvas/app.py web/src/api/admin.ts web/src/pages/admin/users.tsx web/src/pages/admin/models.tsx web/src/router.tsx tests/server/test_admin_api.py tests/server/test_model_assignments.py web/src/test/admin-pages.test.tsx
git commit -m "feat: add admin model assignments"
```

### Task 5: Server-Authoritative Project Persistence

**Files:**
- Create: `server/ai_creation_canvas/api/projects.py`
- Create: `web/src/api/projects.ts`
- Create: `web/src/features/projects/project-sync.ts`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `web/src/stores/canvas/use-canvas-store.ts`
- Modify: `web/src/pages/canvas/index.tsx`
- Test: `tests/server/test_projects_api.py`
- Test: `web/src/test/project-sync.test.ts`
- Test: `web/src/test/canvas-scope-race.test.ts`

**Interfaces:**
- Produces: `GET /api/v1/projects`, `POST /api/v1/projects`, `GET /api/v1/projects/{id}`, `PUT /api/v1/projects/{id}`, `DELETE /api/v1/projects/{id}`.
- Produces: `ProjectEnvelope = { project: CanvasProject; version: number }` and `ProjectSync.activate(scope: ScopedStoreLease): Promise<void>`, `save(project: CanvasProject, expectedVersion: number): Promise<ProjectEnvelope>`.
- Preserves: scoped localForage as an offline draft cache; SQLite becomes the cross-device authority.

- [ ] **Step 1: Write red owner, bound and conflict tests**

```python
def test_projects_are_owned_bounded_and_versioned(local_app):
    created = local_app.user_a.post("/api/v1/projects", headers=local_app.user_a.csrf, json=project_body("p-1", "A"))
    assert created.status_code == 201
    assert local_app.user_b.get("/api/v1/projects/p-1").status_code == 404
    version = created.json()["version"]
    assert local_app.user_a.put("/api/v1/projects/p-1", headers=local_app.user_a.csrf, json={**project_body("p-1", "B"), "expected_version": version}).status_code == 200
    conflict = local_app.user_a.put("/api/v1/projects/p-1", headers=local_app.user_a.csrf, json={**project_body("p-1", "C"), "expected_version": version})
    assert conflict.status_code == 409
```

Also reject documents over 1 MiB, depth over 32, more than 1,000 nodes or 2,000 connections, non-finite numbers, mismatched body/path IDs and unknown fields.

- [ ] **Step 2: Run red tests**

Run: `pytest -q tests/server/test_projects_api.py`

Expected: 404 because project routes do not exist.

- [ ] **Step 3: Add project schema and CRUD**

```sql
CREATE TABLE IF NOT EXISTS canvas_projects (
  project_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  document_json TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, project_id)
);
```

Use strict Pydantic request models for stable metadata and a bounded JSON validator for the document. Updates execute `UPDATE ... SET version=version+1 ... WHERE user_id=? AND project_id=? AND version=?`; zero updated rows returns safe `PROJECT_CONFLICT` 409. Foreign-owner and missing rows both return 404.

- [ ] **Step 4: Write project-sync red tests**

```ts
it("does not let a late user-A load replace user-B projects", async () => {
  const a = deferred<ProjectEnvelope[]>();
  api.list.mockReturnValueOnce(a.promise).mockResolvedValueOnce([projectFor("b")]);
  const activationA = sync.activate(scopeA);
  await sync.activate(scopeB);
  a.resolve([projectFor("a")]);
  await activationA;
  expect(useCanvasStore.getState().projects.map((item) => item.id)).toEqual(["b"]);
});
```

- [ ] **Step 5: Implement server-authoritative sync**

On scope activation, fetch server projects, merge only a same-scope unsynced draft whose `updatedAt` is newer, and populate the store under a captured lease. Create/delete/rename/update queue a 400 ms same-scope save. On 409, fetch the server version, preserve the local draft under a conflict copy title, then show a safe conflict message; never silently overwrite. Logout cancels pending saves.

- [ ] **Step 6: Verify persistence and scope safety**

Run: `pytest -q tests/server/test_projects_api.py && npm test --prefix web -- --run src/test/project-sync.test.ts src/test/canvas-scope-race.test.ts && npm run typecheck --prefix web`

Expected: all pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add server/ai_creation_canvas/api/projects.py server/ai_creation_canvas/storage/sqlite.py server/ai_creation_canvas/app.py web/src/api/projects.ts web/src/features/projects/project-sync.ts web/src/stores/canvas/use-canvas-store.ts web/src/pages/canvas/index.tsx tests/server/test_projects_api.py web/src/test/project-sync.test.ts web/src/test/canvas-scope-race.test.ts
git commit -m "feat: persist owned canvas projects"
```

### Task 6: No-Cost Demo Model Adapter and Protected Result

**Files:**
- Create: `server/ai_creation_canvas/adapters/demo.py`
- Modify: `server/ai_creation_canvas/auth/local.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Test: `tests/contracts/test_demo_adapter.py`
- Test: `tests/integration/test_demo_generation.py`

**Interfaces:**
- Produces: `DemoGenerationAdapter.service_id == "demo-image"` implementing `GenerationPort` plus `open_result(...)`.
- Produces model: `demo-image-v1`, display name `本地演示图片`, operation `image.generate`, input `text`, schema with `aspect_ratio` enum `square`, `portrait`, `landscape`.
- Consumes: model assignment wrapper from Task 4 and existing jobs/results endpoints.

- [ ] **Step 1: Write red adapter contract tests**

```python
@pytest.mark.asyncio
async def test_demo_adapter_is_offline_idempotent_and_range_capable():
    adapter = DemoGenerationAdapter()
    models = await adapter.list_models(context("user-a"))
    assert models[0].model_id == "demo-image-v1"
    first = await adapter.submit(context("user-a"), request("same-key"))
    second = await adapter.submit(context("user-a"), request("same-key"))
    assert first.upstream_job_id == second.upstream_job_id
    assert (await adapter.poll(context("user-a"), first.upstream_job_id)).status is JobStatus.SUCCEEDED
    state = await adapter.poll(context("user-a"), first.upstream_job_id)
    assert state.result is not None
    ranged = await adapter.open_result(context("user-a"), state.result.asset_id, cookie_header="", range_header="bytes=0-9", head=False)
    assert ranged.status_code == 206
    assert ranged.headers["content-length"] == "10"
```

Patch `httpx.AsyncClient.send` or the project Portal client to raise if invoked, proving Demo makes no network request.

- [ ] **Step 2: Run red tests**

Run: `pytest -q tests/contracts/test_demo_adapter.py tests/integration/test_demo_generation.py`

Expected: import fails because the Demo adapter does not exist.

- [ ] **Step 3: Add deterministic local adapter**

The adapter derives a stable opaque upstream ID from `SHA-256(user_id + idempotency_key)` and returns `queued` from submit. Poll returns `succeeded` with an opaque result ID. `open_result` serves the package resource `server/ai_creation_canvas/static/demo-result.png` in both runtime and tests. It validates GET/HEAD/single Range and returns only `image/png`, content length, content range, ETag and accept-ranges. It never embeds prompt text, username or task ID into the media.

Add package data configuration for the single PNG resource and include its SHA-256 in the contract test so accidental replacement is visible.

- [ ] **Step 4: Register only under explicit local flag**

`Settings.enable_demo_adapter` defaults to `False`. `create_app` registers Demo only when true. `serve-local --bootstrap-if-empty` calls `bootstrap_accounts(("demo-image-v1",))`, so only a newly created `canvas-user` receives the default assignment; restarting cannot recreate an assignment an administrator removed. Production and signed Portal modes do not enable or assign Demo implicitly. No Demo code path accepts a Key or URL.

- [ ] **Step 5: Test complete API flow and isolation**

The integration test logs in as user A, lists the assigned Demo model, creates a job, polls to success, reads full/HEAD/Range results, refreshes the same job without a second reservation, and verifies user B gets 404 for the job and result. Assert `canvas_jobs` contains exactly one row for the idempotency key.

Run: `pytest -q tests/contracts/test_demo_adapter.py tests/integration/test_demo_generation.py tests/server/test_models_api.py tests/server/test_asset_security.py`

Expected: all pass.

- [ ] **Step 6: Commit Task 6**

```bash
git add server/ai_creation_canvas/adapters/demo.py server/ai_creation_canvas/static/demo-result.png server/ai_creation_canvas/auth/local.py server/ai_creation_canvas/config.py server/ai_creation_canvas/app.py server/ai_creation_canvas/storage/sqlite.py pyproject.toml tests/contracts/test_demo_adapter.py tests/integration/test_demo_generation.py
git commit -m "feat: add offline demo generation adapter"
```

### Task 7: Hybrid Studio Canvas and Generation Task Tray

**Files:**
- Create: `web/src/components/canvas/generation-inspector.tsx`
- Create: `web/src/components/canvas/generation-node-card.tsx`
- Modify: `web/src/components/layout/task-tray.tsx`
- Modify: `web/src/features/generation/use-generation-job.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/router.tsx`
- Test: `web/src/test/studio-page.test.tsx`
- Test: `web/src/test/generation-job.test.tsx`
- Test: `web/src/test/canvas-generation-page.test.tsx`

**Interfaces:**
- Consumes: assigned `/models`, server-backed project store and existing `useGenerationJob` idempotent resume.
- Produces: `GenerationInspector`, `GenerationNodeCard`, and a task registry `{jobId, title, status, sourceNodeId}` exposed to `TaskTray`.
- Preserves: standard JSON Schema primitive/enum validation and legitimate values including `""`, `false`, `0` and cleared optional numeric fields.

- [ ] **Step 1: Write page-level red tests using the real user actions**

```tsx
it("creates one demo job and one result node through the studio", async () => {
  renderStudio({ models: [demoModel], createJob: queuedJob("job-1"), poll: succeededJob("job-1") });
  await user.type(screen.getByLabelText("提示词"), "黑绿科技产品海报");
  await user.selectOptions(screen.getByLabelText("模型"), "demo-image-v1");
  await user.click(screen.getByRole("button", { name: "加入任务队列" }));
  expect(api.createJob).toHaveBeenCalledTimes(1);
  expect(await screen.findByTestId("result-node-job-1")).toBeVisible();
  expect(screen.getAllByTestId("result-node-job-1")).toHaveLength(1);
});
```

Also test refresh restoration performs GET only, model display names do not control operations, invalid schema values cannot POST, a late result from the previous user cannot update the new scope, and the bottom tray shows each concurrent job independently.

- [ ] **Step 2: Run red tests**

Run: `npm test --prefix web -- --run src/test/studio-page.test.tsx src/test/generation-job.test.tsx src/test/canvas-generation-page.test.tsx`

Expected: fail because the hybrid studio components and multi-job tray registry do not exist.

- [ ] **Step 3: Extract the inspector without changing job semantics**

`GenerationInspector` receives `{models, operation, prompt, params, disabled, onChange, onSubmit}`. It renders only controls returned by `parameterControls()`, initializes valid defaults once, treats only `undefined` as missing, preserves empty strings, converts cleared numeric inputs to `undefined`, and rebuilds `safeParams` from the control allowlist before submit.

- [ ] **Step 4: Render typed generation and result nodes**

`GenerationNodeCard` maps node metadata status to visible label plus icon. Result images use the existing protected same-origin `result_url` inside `.media-surface`; failed nodes show safe text, request ID and a retry button only when the stored ref has a retry token. Do not render provider URLs or raw error objects.

- [ ] **Step 5: Extend the hook to publish a multi-job snapshot**

Keep one `AbortController` per job and the current scoped pending-ref persistence. Add a read-only task registry updated on submitting, queued, running, succeeded and failed; terminal tasks remain in the tray for the current session until dismissed. Scope activation clears the snapshot before restoring the new scope. A restore ref with `jobId` can only call `fetchJob`; it cannot call `createJob`.

- [ ] **Step 6: Assemble the hybrid studio**

The page places a released node palette on the left, Infinite Canvas in the center, `GenerationInspector` on the right and the shared `TaskTray` at the bottom. Slice 1 palette exposes only “提示词” and “图片生成”; video, Dreamina, portrait, ComfyUI and Skill nodes are absent until their slices. Existing canvas zoom/minimap/project functions remain available. The product main container reserves `--task-tray-height` and uses a solid panel behind any future iframe.

- [ ] **Step 7: Run web behavior and type gates**

Run: `npm test --prefix web -- --run src/test/studio-page.test.tsx src/test/generation-job.test.tsx src/test/canvas-generation-page.test.tsx src/test/model-picker.test.ts && npm run typecheck --prefix web && npm run build --prefix web`

Expected: all tests pass; build may retain the already documented bundle-size warning but must exit 0.

- [ ] **Step 8: Commit Task 7**

```bash
git add web/src/components/canvas/generation-inspector.tsx web/src/components/canvas/generation-node-card.tsx web/src/components/layout/task-tray.tsx web/src/features/generation/use-generation-job.ts web/src/pages/canvas/project.tsx web/src/router.tsx web/src/test/studio-page.test.tsx web/src/test/generation-job.test.tsx web/src/test/canvas-generation-page.test.tsx
git commit -m "feat: deliver hybrid demo generation studio"
```

### Task 8: One-Command Local Run and Slice 1 Acceptance

**Files:**
- Create: `scripts/run-local.sh`
- Create: `tests/integration/test_slice1_product.py`
- Create: `web/src/test/slice1-role-flow.test.tsx`
- Modify: `.gitignore`
- Modify: `server/ai_creation_canvas/__main__.py`
- Modify: `docs/operations.md`
- Modify: `docs/verification.md`
- Modify: `scripts/build-release.sh`

**Interfaces:**
- Produces: `bash scripts/run-local.sh` as the single local entry point, defaulting to `127.0.0.1:8992`, `.local-data/`, local identity and Demo adapter.
- Produces: `--open` behavior that opens `http://127.0.0.1:8992/login` only after FastAPI startup.
- Preserves: release build integrity, production Python-only runtime and existing Portal deployment profile.

- [ ] **Step 1: Write the end-to-end API acceptance test**

```python
def test_slice1_admin_user_project_assignment_and_demo_result(slice1_app):
    admin = slice1_app.login("canvas-admin", slice1_app.admin_password)
    user = slice1_app.login("canvas-user", slice1_app.user_password)
    assert admin.get("/api/v1/admin/users").status_code == 200
    assert user.get("/api/v1/admin/users").status_code == 404
    assert [m["model_id"] for m in user.get("/api/v1/models").json()["models"]] == ["demo-image-v1"]
    project = user.post("/api/v1/projects", headers=user.csrf, json=project_body("local-1", "首个项目"))
    job = user.post("/api/v1/jobs", headers=user.csrf, json=job_payload("demo-image-v1"))
    done = user.get(f"/api/v1/jobs/{job.json()['id']}")
    assert done.json()["status"] == "succeeded"
    assert user.get(done.json()["result_url"]).headers["content-type"] == "image/png"
    assert user.get("/api/v1/projects").json()["projects"][0]["project"]["title"] == "首个项目"
```

The same test uses a second temporary ordinary user and proves project, job and result return 404 across owners.

- [ ] **Step 2: Run the acceptance test and confirm any missing wiring fails**

Run: `pytest -q tests/integration/test_slice1_product.py`

Expected: fail because `serve-local`, bootstrap model assignment or one of the complete Slice 1 routes is not wired yet. Preserve this failing output as the red baseline; after Step 3 it must pass without network access.

- [ ] **Step 3: Add one-command local launcher**

```sh
#!/bin/sh
set -eu
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AICC_LOCAL_DATA=${AICC_LOCAL_DATA:-"$REPO_ROOT/.local-data"}
AICC_LOCAL_PORT=${AICC_LOCAL_PORT:-8992}

test "$AICC_LOCAL_PORT" -ne 8991
npm ci --prefix "$REPO_ROOT/web"
npm run build --prefix "$REPO_ROOT/web"
PYTHONPATH="$REPO_ROOT/server" exec python3 -m ai_creation_canvas serve-local \
  --port "$AICC_LOCAL_PORT" --data-dir "$AICC_LOCAL_DATA" \
  --static-dir "$REPO_ROOT/web/dist" --bootstrap-if-empty --open
```

The final script uses only task-specific environment names, rejects production ports and production repository paths through `Settings`, and never prints existing passwords. `serve-local` performs bootstrap before serving, prints new credentials once, schedules `webbrowser.open()` from the FastAPI startup lifecycle, then starts Uvicorn on `127.0.0.1`.

- [ ] **Step 4: Keep local state and credentials out of Git and release**

Add `/.local-data/` to `.gitignore`. Extend security and release tests to assert local databases, session cookies, generated account output and Demo test state are absent from the release. Package the Demo static PNG because it is public, deterministic and non-sensitive; its manifest hash remains enforced by `build-release.sh`.

- [ ] **Step 5: Add a role-level browser behavior test**

The Vitest test logs in with mocked same-origin responses, asserts admin navigation and model assignment actions, logs out, logs in as normal user, asserts admin routes and links are absent, creates a project, submits Demo once, simulates refresh and asserts recovery uses GET only. It also asserts the login, studio, inspector and task tray retain readable semantic classes at 200% layout width and the tray is outside main scrolling.

Run: `npm test --prefix web -- --run src/test/slice1-role-flow.test.tsx`

Expected: pass.

- [ ] **Step 6: Update operator and verification docs with exact commands**

`docs/operations.md` documents:

```text
bash scripts/run-local.sh
# First run prints canvas-admin and canvas-user one-time passwords.
# Opened URL: http://127.0.0.1:8992/login
```

It also documents password reset via `python -m ai_creation_canvas reset-local-password --data-dir <path> --username <name>`, named Cloudflare Tunnel as a later Slice 6 action, and the fact that no real model is configured in Slice 1. Do not add Quick Tunnel or production deployment commands here.

- [ ] **Step 7: Run complete verification from a clean dependency install**

Run in order:

```bash
AICC_VERIFY_ROOT=$(mktemp -d)
python3 -m venv "$AICC_VERIFY_ROOT/venv"
"$AICC_VERIFY_ROOT/venv/bin/pip" install -e '.[test]'
"$AICC_VERIFY_ROOT/venv/bin/pytest" -q
npm ci --prefix web
npm test --prefix web
npm run typecheck --prefix web
npm run build --prefix web
bash scripts/security-scan.sh
mkdir "$AICC_VERIFY_ROOT/release-parent"
bash scripts/build-release.sh --skip-web-build "$AICC_VERIFY_ROOT/release-parent/ai-creation-canvas-release"
git diff --check
```

Expected: all Python and Web tests pass, typecheck/build/security/release/diff gates exit 0, no real network model call occurs, and the only acceptable build output is the previously documented bundle-size warning.

- [ ] **Step 8: Perform the bounded manual local smoke**

Start `bash scripts/run-local.sh`, verify port `8992` only, and use the browser to perform:

1. Log in as admin, change the initial password, confirm Users and Models pages, and ensure no Key field exists.
2. Log out; log in as normal user and change its initial password.
3. Create a project, enter a prompt, select `本地演示图片`, submit once, observe task tray and result node.
4. Refresh and confirm the project/result remains and no second POST occurs.
5. At 200% zoom and a narrow viewport, confirm text remains visible and the bottom tray does not cover the canvas.
6. Confirm Assets and Tasks show real empty/data states and no admin navigation.
7. Stop only the foreground test process; verify production ports and processes were untouched.

Record screenshots without passwords, Cookie values, CSRF values or local database paths.

- [ ] **Step 9: Commit Task 8**

```bash
git add .gitignore scripts/run-local.sh scripts/build-release.sh server/ai_creation_canvas/__main__.py tests/integration/test_slice1_product.py web/src/test/slice1-role-flow.test.tsx docs/operations.md docs/verification.md
git commit -m "feat: make slice one locally runnable"
```

## Final Review Checkpoint

After Task 8, stop implementation and request independent specification and code-quality review against this plan and the approved product design. Do not connect Cloudflare, add a real Key, run a paid task, merge, push, or start Slice 2 until the user has opened the local product and approved Slice 1.
