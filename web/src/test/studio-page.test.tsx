import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setStorageScope } from "@/storage/scope";


afterEach(() => { cleanup(); vi.restoreAllMocks(); clearStorageScope(); useCanvasStore.setState({ projects: [], hydrated: true }); });

it("assembles the released prompt and image-generation studio around the infinite canvas", async () => {
    await setStorageScope({ environment: "test", userId: "u-a" });
    const projectId = useCanvasStore.getState().createProject("黑绿工作室");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ models: [{ model_id: "demo-image-v1", service_id: "demo-image", display_name: "本地演示图片", operations: ["image.generate"], input_media: ["text"], parameter_schema: { type: "object", properties: { aspect_ratio: { type: "string", enum: ["square", "portrait", "landscape"], default: "landscape" } }, required: ["aspect_ratio"] } }] }), { headers: { "content-type": "application/json" } })));

    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);

    expect(screen.getByTestId("studio-palette")).toBeVisible();
    expect(screen.getByTestId("studio-canvas")).toBeVisible();
    expect(screen.getByTestId("studio-canvas")).toHaveClass("flex-1", "min-h-0");
    expect(screen.getByTestId("generation-inspector")).toBeVisible();
    expect(screen.getByTestId("generation-inspector")).toHaveClass("max-h-[45%]", "lg:max-h-none");
    expect(screen.getByText("提示词节点")).toBeVisible();
    expect(screen.getByText("图片生成节点")).toBeVisible();
    expect(screen.queryByText(/视频生成节点|Dreamina|人像|ComfyUI|Skill/)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("模型")).toHaveValue("demo-image-v1"));
    expect(screen.getByRole("button", { name: "加入任务队列" })).toBeDisabled();
});
