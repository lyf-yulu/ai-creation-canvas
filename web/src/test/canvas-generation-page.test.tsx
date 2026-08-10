import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setScopedStoreFactoryForTest, setStorageScope } from "@/storage/scope";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

afterEach(() => { cleanup(); vi.restoreAllMocks(); clearStorageScope(); setScopedStoreFactoryForTest(); useCanvasStore.setState({ projects: [], projectSyncMetadata: {}, syncNotice: null, hydrated: true }); });

it("submits canvas image generation through jobs and writes its result node", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const projectId = useCanvasStore.getState().createProject("Canvas");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ models: [{ model_id: "real-video-looking-image", service_id: "s", display_name: "Video Model", operations: ["image.generate"], input_media: ["text"], parameter_schema: { steps: { type: "integer", default: 4 } } }] }), { headers: { "content-type": "application/json" } })).mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1", status: "queued" }), { status: 201, headers: { "content-type": "application/json" } })).mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1", status: "succeeded", result_url: "/api/v1/results/r-1" }), { headers: { "content-type": "application/json" } })));
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "a cat" } });
    await waitFor(() => expect(screen.getByLabelText("模型")).toHaveValue("real-video-looking-image"));
    await waitFor(() => expect(screen.getByLabelText("steps")).toHaveValue("4"));
    fireEvent.change(screen.getByLabelText("steps"), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "加入任务队列" }));
    await waitFor(() => expect(useCanvasStore.getState().openProject(projectId)?.nodes.some((node) => node.metadata?.sourceJobId === "job-1")).toBe(true));
    expect(await screen.findByTestId("result-node-job-1")).toBeVisible();
    expect(screen.getAllByTestId("result-node-job-1")).toHaveLength(1);
    const [path, request] = (fetch as any).mock.calls[1];
    expect(path).toBe("/api/v1/jobs");
    expect(request.method).toBe("POST");
    expect(JSON.parse(request.body).model_id).toBe("real-video-looking-image");
    expect(JSON.parse(request.body).params.steps).toBe(6);
});

it("submits canvas video generation through jobs and writes a video result node", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const projectId = useCanvasStore.getState().createProject("Canvas");
    vi.stubGlobal("fetch", vi.fn()
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [
            { model_id: "image-model", service_id: "images", display_name: "图片模型", operations: ["image.generate"], input_media: ["text"], parameter_schema: {} },
            { model_id: "video-model", service_id: "videos", display_name: "视频模型", operations: ["video.generate"], input_media: ["text"], parameter_schema: { duration: { type: "integer", default: 5, minimum: 3, maximum: 8 } } },
        ] }), { headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ id: "video-job-1", status: "queued" }), { status: 201, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ id: "video-job-1", operation: "video.generate", status: "succeeded", result_url: "/api/v1/results/video-job-1" }), { headers: { "content-type": "application/json" } })));
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "视频生成" }));
    expect(screen.getByText("VIDEO GENERATION")).toBeVisible();
    expect(screen.getByRole("heading", { name: "视频生成" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "a cloud moving slowly" } });
    await waitFor(() => expect(screen.getByLabelText("模型")).toHaveValue("video-model"));
    fireEvent.click(screen.getByRole("button", { name: "加入任务队列" }));
    await waitFor(() => expect(useCanvasStore.getState().openProject(projectId)?.nodes.some((node) => node.metadata?.sourceJobId === "video-job-1")).toBe(true));
    const result = useCanvasStore.getState().openProject(projectId)?.nodes.find((node) => node.metadata?.sourceJobId === "video-job-1");
    expect(result?.type).toBe(CanvasNodeType.Video);
    expect(await screen.findByLabelText("生成视频结果")).toHaveAttribute("src", "/api/v1/results/video-job-1");
    const [, request] = (fetch as any).mock.calls[1];
    expect(JSON.parse(request.body).operation).toBe("video.generate");
    expect(JSON.parse(request.body).model_id).toBe("video-model");
});

it("writes a safe failure node for a rate-limited generation", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const projectId = useCanvasStore.getState().createProject("Canvas");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ models: [{ model_id: "image", service_id: "s", display_name: "Image", operations: ["image.generate"], input_media: ["text"], parameter_schema: {} }] }), { headers: { "content-type": "application/json" } })).mockResolvedValue(new Response(JSON.stringify({ code: "rate_limited", message: "raw", retryable: true, request_id: "req-1", phase: "submit" }), { status: 429, headers: { "content-type": "application/json" } })));
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "private prompt" } });
    await waitFor(() => expect(screen.getByLabelText("模型")).toHaveValue("image"));
    fireEvent.click(screen.getByRole("button", { name: "加入任务队列" }));
    await waitFor(() => expect(useCanvasStore.getState().openProject(projectId)?.nodes.some((node) => node.metadata?.status === "error")).toBe(true));
    expect(screen.getAllByText("请求过于频繁，请稍后重试。")).not.toHaveLength(0);
});

it("restores a pending result into its source project instead of the currently open project", async () => {
    const sourceProjectId = useCanvasStore.getState().createProject("Source Canvas");
    const otherProjectId = useCanvasStore.getState().createProject("Other Canvas");
    useCanvasStore.getState().updateProject(sourceProjectId, {
        nodes: [{ id: "source-a", type: "config", title: "图片生成", position: { x: 10, y: 20 }, width: 300, height: 140, metadata: { status: "loading" } }],
    });
    setScopedStoreFactoryForTest(() => ({
        getItem: async () => [{
            jobId: "job-from-source",
            projectId: sourceProjectId,
            sourceNodeId: "source-a",
            request: { operation: "image.generate", model_id: "image", prompt: "source prompt", params: {}, asset_ids: [], idempotency_key: "source-key" },
        }],
        setItem: async () => undefined,
        removeItem: async () => undefined,
        iterate: async () => undefined,
    }) as never);
    await setStorageScope({ environment: "test", userId: "u-a" });
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
        const path = String(input);
        if (path === "/api/v1/models") return new Response(JSON.stringify({ models: [] }), { headers: { "content-type": "application/json" } });
        if (path === "/api/v1/jobs/job-from-source") return new Response(JSON.stringify({ id: "job-from-source", status: "succeeded", result_url: "/api/v1/results/source-result" }), { headers: { "content-type": "application/json" } });
        throw new Error(`unexpected request: ${path}`);
    }));

    render(<MemoryRouter initialEntries={[`/canvas/${otherProjectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);

    await waitFor(() => expect(useCanvasStore.getState().openProject(sourceProjectId)?.nodes.some((node) => node.metadata?.sourceJobId === "job-from-source")).toBe(true));
    expect(useCanvasStore.getState().openProject(otherProjectId)?.nodes.some((node) => node.metadata?.sourceJobId === "job-from-source")).toBe(false);
});

it("keeps a concurrently appended generation result when a drag frame commits", () => {
    const projectId = useCanvasStore.getState().createProject("Canvas");
    const source: CanvasNodeData = { id: "source-a", type: CanvasNodeType.Config, title: "Source", position: { x: 10, y: 20 }, width: 300, height: 140 };
    useCanvasStore.getState().updateProject(projectId, { nodes: [source] });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ models: [] }), { headers: { "content-type": "application/json" } })));
    let dragFrame: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
        dragFrame = callback;
        return 1;
    });
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);

    fireEvent.pointerDown(screen.getByTestId("draggable-node-source-a"), { button: 0, pointerId: 1, clientX: 10, clientY: 20 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 60, clientY: 70 });
    const result: CanvasNodeData = { id: "result-a", type: CanvasNodeType.Image, title: "Result", position: { x: 100, y: 120 }, width: 320, height: 180, metadata: { status: "success", sourceJobId: "job-concurrent", content: "/api/v1/results/result-a" } };
    act(() => {
        const latest = useCanvasStore.getState().openProject(projectId)!;
        useCanvasStore.getState().updateProject(projectId, { nodes: [...latest.nodes, result] });
        dragFrame?.(0);
    });

    const nodes = useCanvasStore.getState().openProject(projectId)!.nodes;
    expect(nodes.find((node) => node.id === "source-a")?.position).toEqual({ x: 60, y: 70 });
    expect(nodes.find((node) => node.id === "result-a")?.metadata?.sourceJobId).toBe("job-concurrent");
});
