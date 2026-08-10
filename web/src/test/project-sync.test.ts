import { afterEach, expect, it, vi } from "vitest";

import { ProjectSync, type ProjectApi, type ProjectEnvelope } from "@/features/projects/project-sync";
import { captureAppStorageLease } from "@/lib/localforage-storage";
import { clearCanvasInMemory, type CanvasProject, useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setStorageScope } from "@/storage/scope";


function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
}

function projectFor(id: string, title = id, updatedAt = "2026-08-10T00:00:00.000Z"): CanvasProject {
    return { id, title, createdAt: updatedAt, updatedAt, nodes: [], connections: [], chatSessions: [], activeChatId: null, backgroundMode: "lines", showImageInfo: false, viewport: { x: 0, y: 0, k: 1 } };
}

function envelope(project: CanvasProject, version = 1): ProjectEnvelope { return { project, version }; }

function mockApi(overrides: Partial<ProjectApi> = {}): ProjectApi {
    return {
        list: vi.fn(async () => []),
        create: vi.fn(async (project) => envelope(project)),
        get: vi.fn(async (id) => envelope(projectFor(id))),
        update: vi.fn(async (project, version) => envelope(project, version + 1)),
        remove: vi.fn(async () => undefined),
        ...overrides,
    };
}

afterEach(() => {
    vi.useRealTimers();
    clearCanvasInMemory();
    clearStorageScope();
});

it("does not let a late user-A load replace user-B projects", async () => {
    const a = deferred<ProjectEnvelope[]>();
    const api = mockApi({ list: vi.fn().mockReturnValueOnce(a.promise).mockResolvedValueOnce([envelope(projectFor("b"))]) });
    const sync = new ProjectSync(api, useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    const activationA = sync.activate(captureAppStorageLease()!);

    await setStorageScope({ environment: "test", userId: "user-b" });
    await sync.activate(captureAppStorageLease()!);
    a.resolve([envelope(projectFor("a"))]);
    await activationA;

    expect(useCanvasStore.getState().projects.map((item) => item.id)).toEqual(["b"]);
    sync.stop();
});

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

it("debounces a create and then saves the returned version", async () => {
    vi.useFakeTimers();
    const api = mockApi();
    const sync = new ProjectSync(api, useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    await sync.activate(captureAppStorageLease()!);

    const id = useCanvasStore.getState().createProject("新项目");
    await vi.advanceTimersByTimeAsync(400);
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ id, title: "新项目" }), expect.any(AbortSignal));

    useCanvasStore.getState().renameProject(id, "改名项目");
    await vi.advanceTimersByTimeAsync(400);
    expect(api.update).toHaveBeenCalledWith(expect.objectContaining({ id, title: "改名项目" }), 1, expect.any(AbortSignal));
    sync.stop();
});

it("saves only the final viewport after rapid viewport updates", async () => {
    vi.useFakeTimers();
    const api = mockApi();
    const sync = new ProjectSync(api, useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    await sync.activate(captureAppStorageLease()!);
    const id = useCanvasStore.getState().createProject("Viewport project");
    await vi.advanceTimersByTimeAsync(400);
    vi.mocked(api.create).mockClear();

    useCanvasStore.getState().updateProject(id, { viewport: { x: 20, y: -10, k: 1.1 } });
    useCanvasStore.getState().updateProject(id, { viewport: { x: 50, y: -25, k: 1.25 } });
    useCanvasStore.getState().updateProject(id, { viewport: { x: 120, y: -45, k: 1.75 } });

    await vi.advanceTimersByTimeAsync(400);

    expect(api.update).toHaveBeenCalledTimes(1);
    expect(api.update).toHaveBeenCalledWith(
        expect.objectContaining({ id, viewport: { x: 120, y: -45, k: 1.75 } }),
        1,
        expect.any(AbortSignal),
    );
    sync.stop();
});

it("does not apply a completed user-A save after user-B activates", async () => {
    vi.useFakeTimers();
    const pendingCreate = deferred<ProjectEnvelope>();
    const api = mockApi({ create: vi.fn(() => pendingCreate.promise) });
    const sync = new ProjectSync(api, useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    await sync.activate(captureAppStorageLease()!);
    const aProject = projectFor("a", "A only");
    useCanvasStore.getState().replaceProjects([aProject]);
    await vi.advanceTimersByTimeAsync(400);

    await setStorageScope({ environment: "test", userId: "user-b" });
    useCanvasStore.getState().replaceProjects([]);
    await sync.activate(captureAppStorageLease()!);
    pendingCreate.resolve(envelope(aProject));
    await Promise.resolve();

    expect(useCanvasStore.getState().projects).toEqual([]);
    sync.stop();
});

it("preserves a local conflict copy instead of overwriting it", async () => {
    vi.useFakeTimers();
    const server = projectFor("p-1", "服务端版本", "2026-08-10T00:00:01.000Z");
    const conflict = Object.assign(new Error("conflict"), { code: "PROJECT_CONFLICT" });
    const api = mockApi({
        list: vi.fn(async () => [envelope(projectFor("p-1", "初始版本"), 2)]),
        update: vi.fn(async () => { throw conflict; }),
        get: vi.fn(async () => envelope(server, 3)),
    });
    const sync = new ProjectSync(api, useCanvasStore);
    await setStorageScope({ environment: "test", userId: "user-a" });
    await sync.activate(captureAppStorageLease()!);

    useCanvasStore.getState().renameProject("p-1", "本地修改");
    await vi.advanceTimersByTimeAsync(400);

    expect(useCanvasStore.getState().projects.some((item) => item.id === "p-1" && item.title === "服务端版本")).toBe(true);
    expect(useCanvasStore.getState().projects.some((item) => item.id !== "p-1" && item.title === "本地修改（冲突副本）")).toBe(true);
    expect(useCanvasStore.getState().syncNotice).toBe("检测到其他位置的更新，已保留一个冲突副本。");
    sync.stop();
});
