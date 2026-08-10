import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { clearStorageScope, setScopedStoreFactoryForTest, setStorageScope } from "@/storage/scope";
import { useGenerationJob } from "@/features/generation/use-generation-job";
import { appendResultNode, createResultNode } from "@/features/generation/result-node";

afterEach(() => { clearStorageScope(); setScopedStoreFactoryForTest(); });

it("resumes an existing job without submitting another job", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const api = {
        create: vi.fn(),
        fetch: vi.fn().mockResolvedValue({ id: "j-1", status: "succeeded", result_url: "/api/v1/results/r-1" }),
    };
    const { result } = renderHook(() => useGenerationJob({ api: api as any, pollDelayMs: 1 }));

    await act(async () => result.current.resume("j-1"));
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    expect(api.create).not.toHaveBeenCalled();
    expect(api.fetch).toHaveBeenCalledWith("j-1", expect.any(Object));
});

it("reuses the pending idempotency key after an ambiguous submit failure", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const api = {
        create: vi.fn().mockRejectedValueOnce(new TypeError("network")).mockResolvedValue({ id: "j-1", status: "queued" }),
        fetch: vi.fn().mockResolvedValue({ id: "j-1", status: "succeeded", result_url: "/api/v1/results/r-1" }),
    };
    const { result } = renderHook(() => useGenerationJob({ api, pollDelayMs: 1, idempotencyKey: () => "stable-key" }));
    const request = { operation: "image.generate" as const, model_id: "m", prompt: "p", params: {}, asset_ids: [] };

    await act(async () => expect(result.current.submit(request)).rejects.toThrow("network"));
    await act(async () => result.current.submit(request));
    expect(api.create.mock.calls.map(([job]) => job.idempotency_key)).toEqual(["stable-key", "stable-key"]);
});

it("restores this user's saved job references and only polls them", async () => {
    setScopedStoreFactoryForTest(() => ({ getItem: async () => [{ jobId: "j-saved", request: { operation: "video.generate", model_id: "m", prompt: "p", params: {}, asset_ids: [], idempotency_key: "key" } }], setItem: async () => undefined, removeItem: async () => undefined, iterate: async () => undefined }) as never);
    await setStorageScope({ environment: "test", userId: "u-a" });
    const api = { create: vi.fn(), fetch: vi.fn().mockResolvedValue({ id: "j-saved", status: "succeeded", result_url: "/api/v1/results/r" }) };
    renderHook(() => useGenerationJob({ api: api as any, pollDelayMs: 1 }));
    await waitFor(() => expect(api.fetch).toHaveBeenCalledWith("j-saved", expect.any(Object)));
    expect(api.create).not.toHaveBeenCalled();
});

it("cancels an old scope poll and never publishes it into a new scope", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    let resolve!: (job: { id: string; status: "succeeded"; result_url: string }) => void;
    const api = { create: vi.fn(), fetch: vi.fn(() => new Promise((done) => { resolve = done; })) };
    const { result } = renderHook(() => useGenerationJob({ api: api as any, pollDelayMs: 1 }));
    void act(async () => result.current.resume("j-a"));
    await waitFor(() => expect(api.fetch).toHaveBeenCalled());
    await setStorageScope({ environment: "test", userId: "u-b" });
    await act(async () => resolve({ id: "j-a", status: "succeeded", result_url: "/api/v1/results/r-a" }));
    expect(result.current.state.status).not.toBe("succeeded");
});

it("creates typed same-origin result nodes once with a safe source offset", () => {
    const source = { id: "source", type: "text", title: "source", position: { x: 10, y: 20 }, width: 100, height: 100 };
    const image = createResultNode({ id: "image-job", operation: "image.generate", status: "succeeded", result_url: "/api/v1/results/image" }, source);
    const video = createResultNode({ id: "video-job", operation: "video.generate", status: "succeeded", result_url: "/api/v1/results/video" });
    expect(image).toMatchObject({ type: "image", position: { x: 58, y: 68 }, metadata: { content: "/api/v1/results/image", sourceJobId: "image-job" } });
    expect(video).toMatchObject({ type: "video", position: { x: 80, y: 80 } });
    expect(appendResultNode([image], { id: "image-job", operation: "image.generate", status: "succeeded", result_url: "/api/v1/results/image" }, source)).toHaveLength(1);
});
