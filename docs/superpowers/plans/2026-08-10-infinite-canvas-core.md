# Infinite Canvas Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver reliable infinite-canvas panning, pointer-centered zoom, visible reset/scale controls, draggable nodes, and server-restored viewport/node positions.

**Architecture:** Keep `InfiniteCanvas` responsible only for coordinate transforms and background input. Bind its viewport to the existing server-owned `CanvasProject`, add a focused navigation control component, and place all node pointer mechanics in a reusable drag frame so business cards stay presentation-only. Extend `ProjectSync` with an explicit server-list readiness signal so invalid project routes redirect only after authority is known.

**Tech Stack:** React 19, TypeScript 5, Zustand 5, React Router 7, Vitest 4, Testing Library, FastAPI project API, existing 400 ms `ProjectSync` debounce.

## Global Constraints

- Blank-canvas left drag pans; wheel zooms around the pointer; node left drag moves the node.
- Reserve Ctrl/Cmd + blank-canvas drag for a later selection slice; do not implement selection now.
- Clamp zoom to exactly 5%–500%.
- Persist `viewport` and `nodes[].position` inside the existing server-owned project document.
- Keep the existing 400 ms project-save debounce and conflict-copy behavior.
- Do not add a minimap, selection, connections, snapping, auto-layout, models, workflows, ComfyUI, or Skill nodes.
- Do not add a canvas framework or any new runtime dependency.
- Keep all API calls same-origin; do not expose keys, service URLs, remote plugins, or dynamic scripts.
- Preserve desktop, narrow-screen, right-inspector, and bottom-task-tray compatibility.

---

## File Structure

- Create `web/src/features/canvas/viewport.ts`: pure viewport validation, clamping, reset, and pointer-centered zoom math.
- Create `web/src/components/canvas/canvas-navigation-controls.tsx`: scale slider, scale label, and reset button only.
- Create `web/src/components/canvas/draggable-canvas-node.tsx`: generic screen-to-world node drag frame with cancellation cleanup.
- Create `web/src/test/infinite-canvas.test.tsx`: real component tests for pan, zoom, bounds, and control exclusion.
- Create `web/src/test/canvas-node-drag.test.tsx`: node drag scaling, propagation, animation-frame, and cancellation tests.
- Modify `web/src/components/canvas/infinite-canvas.tsx`: consume pure viewport math and expose a stable test identifier.
- Modify `web/src/components/canvas/generation-node-card.tsx`: render presentation content inside the generic positioned drag frame.
- Modify `web/src/pages/canvas/project.tsx`: bind project viewport, render controls/drag frames, and redirect invalid routes after server readiness.
- Modify `web/src/stores/canvas/use-canvas-store.ts`: add explicit `projectsLoaded` state and normalize imported project viewports.
- Modify `web/src/features/projects/project-sync.ts`: drive `projectsLoaded`, preserve final high-frequency state, and leave failed saves retryable.
- Modify `web/src/test/project-sync.test.ts`: readiness, coalescing, failure/retry, and server-authority tests.
- Modify `web/src/test/studio-page.test.tsx`: integrated navigation controls, stored viewport, node dragging, and responsive layout tests.
- Modify `web/src/test/canvas-generation-page.test.tsx`: ensure generation/result behavior still works through the drag frame.
- Modify `docs/verification.md`: add the infinite-canvas real-page acceptance checklist.

---

### Task 1: Pure viewport math and canvas input

**Files:**
- Create: `web/src/features/canvas/viewport.ts`
- Create: `web/src/test/infinite-canvas.test.tsx`
- Modify: `web/src/components/canvas/infinite-canvas.tsx`

**Interfaces:**
- Produces: `MIN_CANVAS_SCALE = 0.05`, `MAX_CANVAS_SCALE = 5`, `RESET_VIEWPORT`, `normalizeViewport(value): ViewportTransform`, `zoomViewportAt(viewport, pointer, deltaY): ViewportTransform`.
- Consumes: existing `ViewportTransform` from `@/types/canvas`.

- [ ] **Step 1: Write failing pure-math tests**

Add tests that name the production behavior directly:

```ts
import { expect, it } from "vitest";
import { normalizeViewport, zoomViewportAt } from "@/features/canvas/viewport";

it("keeps the world point under the pointer fixed while zooming", () => {
    const next = zoomViewportAt(
        { x: 20, y: 30, k: 1 },
        { x: 200, y: 150 },
        -100,
    );
    expect((200 - next.x) / next.k).toBeCloseTo(180);
    expect((150 - next.y) / next.k).toBeCloseTo(120);
});

it("normalizes legacy and hostile viewport values", () => {
    expect(normalizeViewport({ x: Number.NaN, y: 2, k: 99 })).toEqual({ x: 0, y: 0, k: 1 });
    expect(normalizeViewport({ x: 4, y: 5, k: 0.001 })).toEqual({ x: 4, y: 5, k: 0.05 });
    expect(normalizeViewport({ x: 4, y: 5, k: 9 })).toEqual({ x: 4, y: 5, k: 5 });
});
```

- [ ] **Step 2: Run the pure tests and verify RED**

Run: `npm test --prefix web -- --run src/test/infinite-canvas.test.tsx`

Expected: FAIL because `@/features/canvas/viewport` does not exist.

- [ ] **Step 3: Implement viewport math**

Create the pure module with these exact semantics:

```ts
import type { ViewportTransform } from "@/types/canvas";

export const MIN_CANVAS_SCALE = 0.05;
export const MAX_CANVAS_SCALE = 5;
export const RESET_VIEWPORT: ViewportTransform = Object.freeze({ x: 0, y: 0, k: 1 });

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const clampScale = (value: number) => Math.min(MAX_CANVAS_SCALE, Math.max(MIN_CANVAS_SCALE, value));

export function normalizeViewport(value: unknown): ViewportTransform {
    if (!value || typeof value !== "object") return { ...RESET_VIEWPORT };
    const candidate = value as Partial<ViewportTransform>;
    if (!finite(candidate.x) || !finite(candidate.y) || !finite(candidate.k) || candidate.k <= 0) return { ...RESET_VIEWPORT };
    return { x: candidate.x, y: candidate.y, k: clampScale(candidate.k) };
}

export function zoomViewportAt(viewport: ViewportTransform, pointer: { x: number; y: number }, deltaY: number): ViewportTransform {
    const current = normalizeViewport(viewport);
    const nextScale = clampScale(current.k * Math.pow(1.1, -deltaY / 100));
    const worldX = (pointer.x - current.x) / current.k;
    const worldY = (pointer.y - current.y) / current.k;
    return { x: pointer.x - worldX * nextScale, y: pointer.y - worldY * nextScale, k: nextScale };
}
```

- [ ] **Step 4: Add failing real `InfiniteCanvas` event tests**

Add this stateful harness to `infinite-canvas.test.tsx`:

```tsx
function CanvasHarness({ children }: { children?: React.ReactNode }) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [viewport, setViewport] = useState<ViewportTransform>({ x: 0, y: 0, k: 1 });
    return <>
        <output data-testid="viewport">{`${viewport.x},${viewport.y},${viewport.k}`}</output>
        <InfiniteCanvas containerRef={containerRef} viewport={viewport} onViewportChange={setViewport}>{children}</InfiniteCanvas>
    </>;
}
```

Import `React`, `useRef`, `useState`, `fireEvent`, `render`, `screen`, `InfiniteCanvas`, and `ViewportTransform`, then assert:

```tsx
it("pans on blank left drag and does not pan from a node", () => {
    render(<CanvasHarness><div data-node-id="node-a">node</div></CanvasHarness>);
    const canvas = screen.getByTestId("infinite-canvas");
    fireEvent.pointerDown(canvas, { button: 0, clientX: 20, clientY: 30, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 80, clientY: 90, pointerId: 1 });
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(screen.getByTestId("viewport")).toHaveTextContent("60,60,1");

    fireEvent.pointerDown(screen.getByText("node"), { button: 0, clientX: 80, clientY: 90, pointerId: 2 });
    fireEvent.pointerMove(window, { clientX: 140, clientY: 150, pointerId: 2 });
    fireEvent.pointerUp(window, { pointerId: 2 });
    expect(screen.getByTestId("viewport")).toHaveTextContent("60,60,1");
});
```

Also assert wheel events on `[data-canvas-no-zoom]` leave the viewport unchanged and ordinary canvas wheel events use `zoomViewportAt`.

- [ ] **Step 5: Run the event tests and verify RED**

Run: `npm test --prefix web -- --run src/test/infinite-canvas.test.tsx`

Expected: math tests PASS; component tests FAIL because the canvas lacks `data-testid="infinite-canvas"` and still duplicates zoom math.

- [ ] **Step 6: Connect `InfiniteCanvas` to the pure functions**

- Add `data-testid="infinite-canvas"` to its root.
- Replace inline scale and pointer-centered math with `zoomViewportAt(viewport, { x: mouseX, y: mouseY }, event.deltaY)`.
- Keep the existing `requestAnimationFrame` coalescing for pointer moves.
- Add `pointercancel` and window `blur` listeners that execute the same cleanup as `pointerup` without treating cancellation as a deselection click.
- Do not change the Ctrl/Cmd blank-drag reservation.

- [ ] **Step 7: Verify Task 1**

Run:

```bash
npm test --prefix web -- --run src/test/infinite-canvas.test.tsx
npm run typecheck --prefix web
```

Expected: all Task 1 tests PASS; typecheck exits 0.

- [ ] **Step 8: Commit Task 1**

```bash
git add web/src/features/canvas/viewport.ts web/src/components/canvas/infinite-canvas.tsx web/src/test/infinite-canvas.test.tsx
git commit -m "feat: stabilize infinite canvas navigation"
```

---

### Task 2: Server readiness and invalid project routes

**Files:**
- Modify: `web/src/stores/canvas/use-canvas-store.ts`
- Modify: `web/src/features/projects/project-sync.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/test/project-sync.test.ts`
- Modify: `web/src/test/studio-page.test.tsx`

**Interfaces:**
- Produces: `CanvasStore.projectsLoaded: boolean`, `CanvasStore.setProjectsLoaded(loaded: boolean): void`.
- Consumes: existing `ProjectSync.activate(lease)` and React Router `Navigate`.

- [ ] **Step 1: Write failing server-readiness tests**

Add to `project-sync.test.ts`:

```ts
it("marks projects loaded only after the authoritative server list arrives", async () => {
    const pending = deferred<ProjectEnvelope[]>();
    const sync = new ProjectSync(mockApi({ list: vi.fn(() => pending.promise) }), useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    const activation = sync.activate(captureAppStorageLease()!);
    expect(useCanvasStore.getState().projectsLoaded).toBe(false);
    pending.resolve([envelope(projectFor("server-project"))]);
    await activation;
    expect(useCanvasStore.getState().projectsLoaded).toBe(true);
    sync.stop();
});
```

Add a page test that starts with `projectsLoaded: false` and no local project, verifies no redirect, then sets `projectsLoaded: true` and verifies navigation to `/canvas` with a location probe.

Add these helpers to `studio-page.test.tsx` so later tasks use the same route harness:

```tsx
function LocationProbe() {
    return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderProject(id: string) {
    return render(<MemoryRouter initialEntries={[`/canvas/${id}`]}><Routes>
        <Route path="/canvas" element={<><div>project library</div><LocationProbe /></>} />
        <Route path="/canvas/:id" element={<><CanvasProjectPage /><LocationProbe /></>} />
    </Routes></MemoryRouter>);
}
```

Import `useLocation` with the existing React Router test imports.

- [ ] **Step 2: Run readiness tests and verify RED**

Run:

```bash
npm test --prefix web -- --run src/test/project-sync.test.ts src/test/studio-page.test.tsx
```

Expected: FAIL because `projectsLoaded` and route behavior do not exist.

- [ ] **Step 3: Implement authoritative readiness**

In `CanvasStore`:

```ts
projectsLoaded: boolean;
setProjectsLoaded: (loaded: boolean) => void;
```

- Initialize it to `false`.
- Reset it to `false` in `clearCanvasInMemory()`.
- Do not persist it to browser storage.

In `ProjectSync.activate`:

- set `projectsLoaded` to `false` before starting `api.list`;
- set it to `true` only after the active lease replaces projects from the successful server response;
- leave it `false` on network failure, while retaining the existing sync notice;
- set it to `false` in `stop` only when the stopped generation is still the active local scope.

In `CanvasProjectPage`:

- render a neutral loading surface while `projectsLoaded` is false and the project is absent;
- after `projectsLoaded` becomes true, return `<Navigate to="/canvas" replace />` when the project is still absent;
- never redirect a valid project that arrived from the server after local hydration.

- [ ] **Step 4: Verify Task 2**

Run:

```bash
npm test --prefix web -- --run src/test/project-sync.test.ts src/test/studio-page.test.tsx
npm run typecheck --prefix web
```

Expected: readiness and redirect tests PASS; typecheck exits 0.

- [ ] **Step 5: Commit Task 2**

```bash
git add web/src/stores/canvas/use-canvas-store.ts web/src/features/projects/project-sync.ts web/src/pages/canvas/project.tsx web/src/test/project-sync.test.ts web/src/test/studio-page.test.tsx
git commit -m "fix: wait for authoritative canvas projects"
```

---

### Task 3: Persisted viewport and visible navigation controls

**Files:**
- Create: `web/src/components/canvas/canvas-navigation-controls.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/stores/canvas/use-canvas-store.ts`
- Modify: `web/src/test/studio-page.test.tsx`
- Modify: `web/src/test/project-sync.test.ts`

**Interfaces:**
- Produces: `CanvasNavigationControls({ viewport, onViewportChange })`.
- Consumes: `normalizeViewport`, `RESET_VIEWPORT`, `MIN_CANVAS_SCALE`, `MAX_CANVAS_SCALE`, and `CanvasStore.updateProject`.

- [ ] **Step 1: Write failing control and persistence tests**

Add page assertions:

```tsx
it("uses the stored project viewport and exposes scale and reset controls", async () => {
    const id = useCanvasStore.getState().createProject("Stored view");
    useCanvasStore.getState().updateProject(id, { viewport: { x: 120, y: -45, k: 1.75 } });
    renderProject(id);
    expect(screen.getByTestId("canvas-world")).toHaveStyle({ transform: "translate(120px, -45px) scale(1.75)" });
    expect(screen.getByLabelText("画布缩放")).toHaveValue("175");
    fireEvent.click(screen.getByRole("button", { name: "复位画布" }));
    expect(useCanvasStore.getState().openProject(id)?.viewport).toEqual({ x: 0, y: 0, k: 1 });
});
```

Add a `ProjectSync` fake-timer test that updates one project's viewport several times inside 400 ms, advances the timer, and asserts one update containing only the final viewport.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
npm test --prefix web -- --run src/test/studio-page.test.tsx src/test/project-sync.test.ts
```

Expected: FAIL because the product page uses local viewport state and has no navigation controls.

- [ ] **Step 3: Build focused navigation controls**

Create a component with no minimap API:

```tsx
type Props = {
    viewport: ViewportTransform;
    onViewportChange: (viewport: ViewportTransform) => void;
};

export function CanvasNavigationControls({ viewport, onViewportChange }: Props) {
    return <div data-canvas-no-zoom className="absolute bottom-4 left-4 z-20 flex items-center gap-2 rounded-xl border border-[#285038] bg-[#08100be8] px-3 py-2 shadow-xl">
        <button type="button" aria-label="复位画布" onClick={() => onViewportChange({ ...RESET_VIEWPORT })}>复位</button>
        <input aria-label="画布缩放" type="range" min={MIN_CANVAS_SCALE * 100} max={MAX_CANVAS_SCALE * 100} value={Math.round(viewport.k * 100)} onChange={(event) => onViewportChange({ ...viewport, k: Number(event.target.value) / 100 })} />
        <span aria-live="polite">{Math.round(viewport.k * 100)}%</span>
    </div>;
}
```

Use the approved black-green palette, visible focus rings, and a compact width that fits a 240 px viewport.

- [ ] **Step 4: Bind the product page to project viewport**

- Delete the page-local `useState<ViewportTransform>`.
- Derive `viewport = normalizeViewport(project.viewport)`.
- Define one `changeViewport(next)` callback that normalizes the value and calls `updateProject(id, { viewport: normalized })`.
- Pass that callback to both `InfiniteCanvas` and `CanvasNavigationControls`.
- Add `data-testid="canvas-world"` to the transformed world element in `InfiniteCanvas`.
- In `importProject`, normalize `source.viewport` so legacy invalid values cannot enter new project state.

- [ ] **Step 5: Verify Task 3**

Run:

```bash
npm test --prefix web -- --run src/test/infinite-canvas.test.tsx src/test/studio-page.test.tsx src/test/project-sync.test.ts
npm run typecheck --prefix web
```

Expected: controls, stored viewport, reset, normalization, and coalesced final-save tests PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add web/src/components/canvas/canvas-navigation-controls.tsx web/src/components/canvas/infinite-canvas.tsx web/src/pages/canvas/project.tsx web/src/stores/canvas/use-canvas-store.ts web/src/test/studio-page.test.tsx web/src/test/project-sync.test.ts
git commit -m "feat: persist canvas viewport controls"
```

---

### Task 4: Scale-correct reusable node dragging

**Files:**
- Create: `web/src/components/canvas/draggable-canvas-node.tsx`
- Create: `web/src/test/canvas-node-drag.test.tsx`
- Modify: `web/src/components/canvas/generation-node-card.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/test/canvas-generation-page.test.tsx`

**Interfaces:**
- Produces: `DraggableCanvasNode({ node, scale, onPositionChange, children })` where `onPositionChange(nodeId: string, position: Position): void`.
- Consumes: normalized `viewport.k`, `CanvasNodeData`, and `CanvasStore.updateProject`.

- [ ] **Step 1: Write failing drag tests**

Cover 50%, 100%, and 200% scale with a table:

```tsx
function nodeAt(x: number, y: number): CanvasNodeData {
    return { id: "node-a", type: CanvasNodeType.Text, title: "Node A", position: { x, y }, width: 200, height: 100 };
}

it.each([
    [0.5, 100],
    [1, 50],
    [2, 25],
])("moves by screen delta divided by scale %s", (scale, expectedWorldDelta) => {
    const onPositionChange = vi.fn();
    render(<DraggableCanvasNode node={nodeAt(10, 20)} scale={scale} onPositionChange={onPositionChange}><button>child action</button></DraggableCanvasNode>);
    fireEvent.pointerDown(screen.getByTestId("draggable-node-node-a"), { button: 0, pointerId: 1, clientX: 40, clientY: 50 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 90, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(onPositionChange).toHaveBeenLastCalledWith("node-a", { x: 10 + expectedWorldDelta, y: 20 + expectedWorldDelta });
});
```

Also assert:

- pointer events do not bubble to a parent pan spy;
- pointer cancel and window blur stop subsequent movement;
- non-left buttons do nothing;
- pointer down on `button,input,textarea,select,a,video,audio` does not start a node drag;
- one animation frame emits at most one position update and uses the latest pointer coordinates.

- [ ] **Step 2: Run drag tests and verify RED**

Run: `npm test --prefix web -- --run src/test/canvas-node-drag.test.tsx`

Expected: FAIL because `DraggableCanvasNode` does not exist.

- [ ] **Step 3: Implement the generic drag frame**

The component must:

- render one absolute wrapper with `data-node-id`, `data-testid`, `left`, `top`, `width`, and `minHeight` from the node;
- start only on left-button pointer down outside interactive descendants;
- call `event.stopPropagation()` before setting pointer capture;
- remember pointer origin and node origin;
- calculate `x = initial.x + (clientX - startX) / normalizedScale` and the same for `y`;
- reject non-finite calculated positions;
- coalesce pointer moves with one `requestAnimationFrame`;
- clean up on pointer up, pointer cancel, window blur, and unmount;
- restore the body cursor after every exit path.

- [ ] **Step 4: Move positioning out of business cards**

- Remove absolute positioning, `left/top/width/minHeight`, and `data-node-id` from `GenerationNodeCard`.
- Keep its content, same-origin media, retry behavior, and test IDs unchanged.
- In `CanvasProjectPage`, render every node inside `DraggableCanvasNode`.
- Implement `moveNode(nodeId, position)` using the latest project from `useCanvasStore.getState()` and one `updateProject(id, { nodes })` call so concurrent generation results are not overwritten by a stale render closure.

- [ ] **Step 5: Verify Task 4**

Run:

```bash
npm test --prefix web -- --run src/test/canvas-node-drag.test.tsx src/test/infinite-canvas.test.tsx src/test/canvas-generation-page.test.tsx src/test/studio-page.test.tsx
npm run typecheck --prefix web
```

Expected: all drag and existing generation-result tests PASS; typecheck exits 0.

- [ ] **Step 6: Commit Task 4**

```bash
git add web/src/components/canvas/draggable-canvas-node.tsx web/src/components/canvas/generation-node-card.tsx web/src/pages/canvas/project.tsx web/src/test/canvas-node-drag.test.tsx web/src/test/canvas-generation-page.test.tsx
git commit -m "feat: drag canvas nodes at any scale"
```

---

### Task 5: Failure recovery, responsive acceptance, and release verification

**Files:**
- Modify: `web/src/features/projects/project-sync.ts`
- Modify: `web/src/test/project-sync.test.ts`
- Modify: `web/src/test/studio-page.test.tsx`
- Modify: `docs/verification.md`

**Interfaces:**
- Consumes: Tasks 1–4 public interfaces.
- Produces: a fully verified infinite-canvas core slice with no new production dependency.

- [ ] **Step 1: Write the save-retry regression test**

Use fake timers and an API whose first update rejects and second update succeeds:

```ts
it("keeps a failed viewport change and retries the latest project on the next edit", async () => {
    vi.useFakeTimers();
    const api = mockApi({
        list: vi.fn(async () => [envelope(projectFor("p-1"), 1)]),
        update: vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockImplementation(async (project, version) => envelope(project, version + 1)),
    });
    const sync = new ProjectSync(api, useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    await sync.activate(captureAppStorageLease()!);
    useCanvasStore.getState().updateProject("p-1", { viewport: { x: 90, y: 40, k: 1.5 } });
    await vi.advanceTimersByTimeAsync(400);
    expect(useCanvasStore.getState().openProject("p-1")?.viewport).toEqual({ x: 90, y: 40, k: 1.5 });
    useCanvasStore.getState().renameProject("p-1", "retry save");
    await vi.advanceTimersByTimeAsync(400);
    expect(api.update).toHaveBeenLastCalledWith(expect.objectContaining({ title: "retry save", viewport: { x: 90, y: 40, k: 1.5 } }), 1, expect.any(AbortSignal));
    sync.stop();
});
```

- [ ] **Step 2: Run the retry and responsive tests**

Run:

```bash
npm test --prefix web -- --run src/test/project-sync.test.ts src/test/studio-page.test.tsx
```

Expected: the retry regression test PASSes because failed updates do not advance `snapshots`. Responsive tests must assert the controls stay inside `studio-canvas` and carry compact classes at narrow breakpoints. If the retry test fails, Step 3 applies the specified minimal correction.

- [ ] **Step 3: Apply only evidence-required recovery changes**

If the retry test fails because a failed snapshot is accidentally marked saved, change `ProjectSync` so `snapshots.set(project.id, localSnapshot)` occurs only after a successful API result. Do not add automatic background retry or a new queue; the next user edit is the approved retry trigger.

- [ ] **Step 4: Update the verification checklist**

Add an “无限画布核心” section to `docs/verification.md` with the exact desktop and narrow-screen sequence from the approved design:

1. create a project and two nodes;
2. pan, zoom to a non-100% value, and drag both nodes;
3. refresh and reopen;
4. confirm viewport and node positions restore;
5. confirm inspector, task tray, and navigation controls do not overlap;
6. confirm no unhandled browser errors and no request storm.

- [ ] **Step 5: Run full automated gates**

Run serially so release tests do not race with `node_modules`:

```bash
.venv/bin/pytest -q
npm ci --prefix web
npm test --prefix web
npm run typecheck --prefix web
npm run build --prefix web
npm audit --prefix web --omit=dev --audit-level=moderate
scripts/security-scan.sh
git diff --check
```

Expected: Python and web suites PASS; typecheck/build/security/diff-check exit 0; npm audit reports 0 vulnerabilities at the selected threshold. The existing Vite large-chunk warning is non-blocking but must be reported.

- [ ] **Step 6: Build and verify both release paths**

```bash
release_root=$(mktemp -d /private/tmp/aicc-canvas-release.XXXXXX)
bash scripts/build-release.sh "$release_root/release"
skip_root=$(mktemp -d /private/tmp/aicc-canvas-skip.XXXXXX)
bash scripts/build-release.sh --skip-web-build "$skip_root/release"
test -f "$release_root/release/web/dist/index.html"
test -f "$skip_root/release/web/dist/index.html"
```

Expected: both commands exit 0 and both static entry files exist.

- [ ] **Step 7: Perform real browser acceptance**

Use the local test account and `http://127.0.0.1:8992`:

- reload after the new static build;
- run the seven-step checklist from the design at desktop width;
- repeat layout and interaction checks at 415 px and 240 px widths;
- inspect browser console errors;
- verify the server receives debounced project saves rather than one request per pointer move;
- remove only the temporary acceptance account/project/job after evidence is captured;
- leave the login page open for the user.

- [ ] **Step 8: Request code review and fix only reproducible blockers**

Dispatch a read-only reviewer over `main...HEAD`. Require exact file/line, reproduction, severity, and Ready verdict. For any Critical/Important issue, use systematic debugging and a failing regression test before production changes. Re-run the affected focused tests after each fix.

- [ ] **Step 9: Re-run fresh final gates and commit**

After review fixes, repeat Step 5, then:

```bash
git add web/src/features/projects/project-sync.ts web/src/test/project-sync.test.ts web/src/test/studio-page.test.tsx docs/verification.md
git commit -m "test: verify infinite canvas core slice"
```

- [ ] **Step 10: Push the feature branch for user review**

```bash
git push -u origin agent/infinite-canvas-core
```

Report the branch, commit SHA, public comparison URL, test counts, browser evidence, and any non-blocking warnings. Do not merge to `main` until the user approves the working infinite-canvas slice.
