import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setStorageScope } from "@/storage/scope";

afterEach(() => { cleanup(); vi.restoreAllMocks(); clearStorageScope(); useCanvasStore.setState({ projects: [], hydrated: true }); });

it("submits canvas image generation through jobs and writes its result node", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const projectId = useCanvasStore.getState().createProject("Canvas");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1", status: "queued" }), { status: 201, headers: { "content-type": "application/json" } })).mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1", status: "succeeded", result_url: "/api/v1/results/r-1" }), { headers: { "content-type": "application/json" } })));
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "a cat" } });
    fireEvent.click(screen.getByRole("button", { name: "生成图片" }));
    await waitFor(() => expect(useCanvasStore.getState().openProject(projectId)?.nodes.some((node) => node.metadata?.sourceJobId === "job-1")).toBe(true));
    const [path, request] = (fetch as any).mock.calls[0];
    expect(path).toBe("/api/v1/jobs");
    expect(request.method).toBe("POST");
});

it("writes a safe failure node for a rate-limited generation", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const projectId = useCanvasStore.getState().createProject("Canvas");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "rate_limited", message: "raw", retryable: true, request_id: "req-1", phase: "submit" }), { status: 429, headers: { "content-type": "application/json" } })));
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "private prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "生成图片" }));
    await waitFor(() => expect(useCanvasStore.getState().openProject(projectId)?.nodes.some((node) => node.metadata?.status === "error")).toBe(true));
    expect(screen.getAllByText("请求过于频繁，请稍后重试。")).not.toHaveLength(0);
});
