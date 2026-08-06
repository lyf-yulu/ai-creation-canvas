import { afterEach, expect, it, vi } from "vitest";

import { clearCanvasInMemory, useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setStorageScope } from "@/storage/scope";

afterEach(() => {
    vi.useRealTimers();
    clearCanvasInMemory();
    clearStorageScope();
});

it("cancels a queued canvas write before a user switch or logout", async () => {
    vi.useFakeTimers();
    await setStorageScope({ environment: "test", userId: "user-a" });
    useCanvasStore.getState().createProject("A only");
    clearCanvasInMemory();
    await setStorageScope({ environment: "test", userId: "user-b" });
    await vi.advanceTimersByTimeAsync(500);
    expect(useCanvasStore.getState().projects).toEqual([]);

    await setStorageScope({ environment: "test", userId: "user-a" });
    useCanvasStore.getState().createProject("logout only");
    clearCanvasInMemory();
    clearStorageScope();
    await vi.advanceTimersByTimeAsync(500);
    expect(useCanvasStore.getState().projects).toEqual([]);
});
