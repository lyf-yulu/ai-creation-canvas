import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { WorkflowImport } from "@/components/comfy/workflow-import";
import AdminComfyWorkflowsPage from "@/pages/admin/comfy-workflows";

const workflow = {
    workflow_id: "cw-1",
    display_name: "Core workflow",
    description: "",
    service_id: "comfy-local",
    lifecycle: { enabled: false, archived: false },
    revision: 1,
    lifecycle_revision: 1,
    checksum_prefix: "a1b2c3d4e5f6",
    execution_available: false,
};

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

it("uploads workflow JSON without reading it in the browser", async () => {
    const file = new File(['{"api_key":"must-not-be-read"}'], "workflow.json", { type: "application/json" });
    const text = vi.spyOn(file, "text");
    const onImported = vi.fn();
    const onImport = vi.fn().mockResolvedValue(workflow);
    render(<WorkflowImport onImport={onImport} onImported={onImported} />);

    const input = screen.getByLabelText("选择工作流 JSON") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("工作流显示名"), { target: { value: "Core workflow" } });
    fireEvent.change(screen.getByLabelText("ComfyUI 服务 ID"), { target: { value: "comfy-local" } });
    fireEvent.click(screen.getByRole("button", { name: "导入工作流" }));

    await waitFor(() => expect(onImport).toHaveBeenCalledWith(file, { displayName: "Core workflow", serviceId: "comfy-local" }));
    expect(text).not.toHaveBeenCalled();
    expect(input.value).toBe("");
    expect(onImported).toHaveBeenCalledWith(workflow);
});

it("prevents duplicate imports and does not show server error details", async () => {
    let rejectImport!: (reason?: unknown) => void;
    const onImport = vi.fn(
        () =>
            new Promise<typeof workflow>((_resolve, reject) => {
                rejectImport = reject;
            }),
    );
    render(<WorkflowImport onImport={onImport} onImported={vi.fn()} />);
    const file = new File(["{}"], "workflow.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("选择工作流 JSON"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("工作流显示名"), { target: { value: "Core workflow" } });
    fireEvent.change(screen.getByLabelText("ComfyUI 服务 ID"), { target: { value: "comfy-local" } });
    const submit = screen.getByRole("button", { name: "导入工作流" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(onImport).toHaveBeenCalledTimes(1);

    rejectImport(new Error("server_url=https://unsafe.example token=must-not-render"));
    expect(await screen.findByRole("alert")).toHaveTextContent("导入失败，请检查工作流 JSON 和服务配置。");
    expect(screen.queryByText(/unsafe|token/i)).not.toBeInTheDocument();
});

it("lists safe workflow projections, previews a selected workflow, and saves user assignments", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const path = String(input);
        if (path === "/api/v1/admin/comfy-workflows") return new Response(JSON.stringify({ workflows: [workflow] }), { status: 200, headers: { "content-type": "application/json" } });
        if (path === "/api/v1/admin/users")
            return new Response(JSON.stringify({ users: [{ user_id: "user-1", username: "maker", display_name: "创作者", role: "user", enabled: true, must_change_password: false, model_ids: [], comfy_workflow_ids: ["cw-1"] }] }), {
                status: 200,
                headers: { "content-type": "application/json" },
            });
        if (path === "/api/v1/admin/comfy-workflows/cw-1")
            return new Response(
                JSON.stringify({
                    ...workflow,
                    current_revision: {
                        workflow_id: "cw-1",
                        revision: 1,
                        formats: ["editor"],
                        checksum_prefix: "a1b2c3d4",
                        preview: { has_editor_layout: true, nodes: [{ id: "1", type: "LoadImage", title: "secret widget", position: [0, 0] }], edges: [] },
                        dependencies: {
                            node_types: [
                                { type: "LoadImage", is_core: true },
                                { type: "CustomNode", is_core: false },
                            ],
                        },
                        execution_available: false,
                    },
                }),
                { status: 200, headers: { "content-type": "application/json" } },
            );
        if (path === "/api/v1/admin/users/user-1/comfy-workflows" && init?.method === "PUT") return new Response(JSON.stringify({ user_id: "user-1", workflow_ids: [] }), { status: 200, headers: { "content-type": "application/json" } });
        throw new Error(`unexpected ${path}`);
    });
    render(<AdminComfyWorkflowsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /Core workflow/ }));
    expect(await screen.findByText("a1b2c3d4")).toBeVisible();
    expect(screen.getByText("LoadImage")).toBeVisible();
    expect(screen.queryByText("secret widget")).not.toBeInTheDocument();
    expect(screen.getByText("核心节点")).toBeVisible();
    expect(screen.getByText("需确认")).toBeVisible();
    expect(screen.getByLabelText("向创作者派发 Core workflow")).toBeChecked();
    fireEvent.click(screen.getByLabelText("向创作者派发 Core workflow"));
    fireEvent.click(screen.getByRole("button", { name: "保存派发" }));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/admin/users/user-1/comfy-workflows", expect.objectContaining({ method: "PUT", body: JSON.stringify({ workflow_ids: [] }) })));
    expect(screen.queryByText(/api_key|server_url|endpoint/i)).not.toBeInTheDocument();
});
