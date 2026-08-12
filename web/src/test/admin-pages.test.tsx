import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ProductShell } from "@/components/layout/product-shell";
import AdminModelsPage from "@/pages/admin/models";
import AdminUsagePage from "@/pages/admin/usage";
import AdminUsersPage from "@/pages/admin/users";
import { useSessionStore } from "@/stores/portal/use-session-store";


const users = [
    { user_id: "admin-1", username: "canvas-admin", display_name: "管理员", role: "admin", enabled: true, must_change_password: false, model_ids: ["image-pro"], created_at: 1, updated_at: 1 },
    { user_id: "user-1", username: "canvas-user", display_name: "普通用户", role: "user", enabled: true, must_change_password: false, model_ids: ["image-pro"], created_at: 1, updated_at: 1 },
];
const models = [
    { model_id: "image-pro", service_id: "image", display_name: "图像 Pro", operations: ["image.generate"], input_media: ["text"], parameter_schema: {} },
    { model_id: "video-fast", service_id: "video", display_name: "视频 Fast", operations: ["video.generate"], input_media: ["text"], parameter_schema: {} },
];

beforeEach(() => {
    useSessionStore.setState({
        session: { user_id: "admin-1", username: "管理员", role: "admin", must_change_password: false },
        environment: "local",
        loading: false,
        errorCode: null,
        logout: vi.fn(),
    });
});

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

it("shows administrator destinations only to an administrator", () => {
    const { rerender } = render(<MemoryRouter><ProductShell><div /></ProductShell></MemoryRouter>);
    expect(screen.getAllByRole("link", { name: "账号管理" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "模型派发" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "使用统计" })).toHaveLength(2);

    useSessionStore.setState({ session: { user_id: "user-1", username: "普通用户", role: "user", must_change_password: false } });
    rerender(<MemoryRouter><ProductShell><div /></ProductShell></MemoryRouter>);
    expect(screen.queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "模型派发" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "使用统计" })).not.toBeInTheDocument();
});

it("shows server-owned per-user image and video usage", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
        totals: { jobs: 4, succeeded: 2, failed: 1, active: 1, image: 2, video: 2 },
        users: [{ user_id: "user-1", username: "canvas-user", display_name: "普通用户", jobs: 3, succeeded: 1, failed: 1, active: 1, image: 2, video: 1 }],
    }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<AdminUsagePage />);

    expect(await screen.findByRole("heading", { name: "使用统计" })).toBeVisible();
    expect(screen.getByText("4", { selector: "strong" })).toBeVisible();
    expect(screen.getByRole("row", { name: /普通用户.*canvas-user.*3.*2.*1.*1.*1/ })).toBeVisible();
});

it("lets an administrator disable an account without exposing sensitive fields", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ users }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ ...users[1], enabled: false }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<AdminUsersPage />);
    expect(await screen.findByRole("button", { name: "停用 canvas-user" })).toBeVisible();
    expect(screen.queryByText(/password|secret|token/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停用 canvas-user" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
        "/api/v1/admin/users/user-1",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ enabled: false }) }),
    ));
    expect(await screen.findByText("已停用")).toBeVisible();
});

it("saves the selected model assignments in one request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ users }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models, diagnostics: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ pools: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ user_id: "user-1", model_ids: ["image-pro", "video-fast"] }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<AdminModelsPage />);
    await screen.findByRole("heading", { name: "模型与调用线路" });
    fireEvent.change(await screen.findByLabelText("选择账号"), { target: { value: "user-1" } });
    fireEvent.click(await screen.findByLabelText("视频 Fast"));
    fireEvent.click(screen.getByRole("button", { name: "保存派发" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
        "/api/v1/admin/users/user-1/models",
        expect.objectContaining({ method: "PUT", body: JSON.stringify({ model_ids: ["image-pro", "video-fast"] }) }),
    ));
    expect(await screen.findByText("派发已保存")).toBeVisible();
});

it("lets an administrator remove an unavailable legacy assignment without re-adding it", async () => {
    const unavailableUsers = [{ ...users[1], model_ids: ["image-pro", "retired-model"] }];
    const fetchMock = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ users: unavailableUsers }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [models[0]], diagnostics: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ pools: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ user_id: "user-1", model_ids: ["image-pro"] }), { status: 200, headers: { "content-type": "application/json" } }));
    render(<AdminModelsPage />);
    fireEvent.change(await screen.findByLabelText("选择账号"), { target: { value: "user-1" } });
    const retired = await screen.findByLabelText("取消不可用模型 retired-model");
    expect(retired).toBeChecked();
    fireEvent.click(retired);
    expect(screen.queryByText(/retired-model/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存派发" }));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/admin/users/user-1/models", expect.objectContaining({ method: "PUT", body: JSON.stringify({ model_ids: ["image-pro"] }) })));
    expect(await screen.findByText("派发已保存")).toBeVisible();
});

it("renders purged model and route projections as read-only audit history", async () => {
    const historicalModel = { model_id: "purged", display_name: "Purged model", modality: "image", enabled: false, archived_at: "2026-08-12T00:00:00Z", revision: 3, created_at: "x", updated_at: "x" };
    const historicalRoute = { route_id: "purged-route", model_id: "purged", enabled: false, archived_at: "2026-08-12T00:00:00Z", revision: 4, created_at: "x", updated_at: "x" };
    vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ users: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [], diagnostics: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [historicalModel] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ pools: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ routes: [historicalRoute] }), { status: 200, headers: { "content-type": "application/json" } }));
    render(<AdminModelsPage />);
    expect(await screen.findByText("只读历史模型")).toBeVisible();
    expect(await screen.findByText("只读历史线路")).toBeVisible();
    expect(screen.queryByRole("button", { name: "新建线路" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("能力模板")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("线路模板")).not.toBeInTheDocument();
    expect(screen.getAllByText(/审计记录永久保留/)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
});

it("does not apply a late model refresh after selecting another model", async () => {
    const contract = { operation: "image.generate", input_ports: [{ port_id: "prompt", media_type: "text", min_items: 1, max_items: 1 }], output_media_type: "image", parameter_schema: { type: "object", properties: {}, additionalProperties: false }, parameter_mappings: {} };
    const a = { model_id: "a", display_name: "Model A", introduction: "A intro", modality: "image", operation_contracts: [contract], enabled: true, archived_at: null, revision: 1 };
    const b = { ...a, model_id: "b", display_name: "Model B", introduction: "B intro" };
    let resolveRefresh!: (response: Response) => void;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = String(input), method = init?.method || "GET";
        if (url === "/api/v1/admin/users") return new Response(JSON.stringify({ users: [] }), { status: 200, headers: { "content-type": "application/json" } });
        if (url === "/api/v1/admin/models") return new Response(JSON.stringify({ models: [], diagnostics: [] }), { status: 200, headers: { "content-type": "application/json" } });
        if (url.includes("credential-pools")) return new Response(JSON.stringify({ pools: [] }), { status: 200, headers: { "content-type": "application/json" } });
        if (url.includes("logical-models?") ) return new Response(JSON.stringify({ models: [a, b] }), { status: 200, headers: { "content-type": "application/json" } });
        if (url.includes("/routes")) return new Response(JSON.stringify({ routes: [] }), { status: 200, headers: { "content-type": "application/json" } });
        if (url === "/api/v1/admin/logical-models/a" && method === "PUT") return new Response(JSON.stringify({ code: "REVISION_CONFLICT", message: "changed", retryable: false, request_id: "r", phase: "request" }), { status: 409, headers: { "content-type": "application/json" } });
        if (url === "/api/v1/admin/logical-models/a") return new Promise<Response>((resolve) => { resolveRefresh = resolve; });
        throw new Error(`unexpected ${method} ${url}`);
    });
    render(<AdminModelsPage />);
    expect(await screen.findByLabelText("模型显示名")).toHaveValue("Model A");
    fireEvent.change(screen.getByLabelText("模型显示名"), { target: { value: "A changed" } });
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新加载" }));
    fireEvent.click(screen.getByText("Model B").closest("button")!);
    expect(screen.getByLabelText("模型显示名")).toHaveValue("Model B");
    resolveRefresh(new Response(JSON.stringify({ ...a, display_name: "A refreshed", revision: 2 }), { status: 200, headers: { "content-type": "application/json" } }));
    await waitFor(() => expect(screen.getByLabelText("模型显示名")).toHaveValue("Model B"));
    expect(fetchMock).toHaveBeenCalled();
});
