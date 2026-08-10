import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setStorageScope } from "@/storage/scope";


afterEach(() => { cleanup(); vi.restoreAllMocks(); clearStorageScope(); useCanvasStore.setState({ projects: [], hydrated: true, projectsLoaded: false }); });

function LocationProbe() {
    return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderProject(id: string) {
    return render(<MemoryRouter initialEntries={[`/canvas/${id}`]}><Routes>
        <Route path="/canvas" element={<><div>project library</div><LocationProbe /></>} />
        <Route path="/canvas/:id" element={<><CanvasProjectPage /><LocationProbe /></>} />
    </Routes></MemoryRouter>);
}

it("waits for the server project list before redirecting a missing project", async () => {
    useCanvasStore.setState({ projects: [], projectsLoaded: false });

    renderProject("server-project");

    expect(screen.getByTestId("location")).toHaveTextContent("/canvas/server-project");
    useCanvasStore.getState().setProjectsLoaded(true);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/canvas"));
});

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

it("uses the stored project viewport and exposes scale and reset controls", () => {
    const id = useCanvasStore.getState().createProject("Stored view");
    useCanvasStore.getState().updateProject(id, { viewport: { x: 120, y: -45, k: 1.75 } });

    renderProject(id);

    expect(screen.getByTestId("canvas-world")).toHaveStyle({ transform: "translate(120px, -45px) scale(1.75)" });
    expect(screen.getByLabelText("画布缩放")).toHaveValue("175");

    fireEvent.click(screen.getByRole("button", { name: "复位画布" }));

    expect(useCanvasStore.getState().openProject(id)?.viewport).toEqual({ x: 0, y: 0, k: 1 });
});
