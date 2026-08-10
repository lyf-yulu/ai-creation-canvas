import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ProductShell } from "@/components/layout/product-shell";
import AdminModelsPage from "@/pages/admin/models";
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

    useSessionStore.setState({ session: { user_id: "user-1", username: "普通用户", role: "user", must_change_password: false } });
    rerender(<MemoryRouter><ProductShell><div /></ProductShell></MemoryRouter>);
    expect(screen.queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "模型派发" })).not.toBeInTheDocument();
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
        .mockResolvedValueOnce(new Response(JSON.stringify({ user_id: "user-1", model_ids: ["image-pro", "video-fast"] }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<AdminModelsPage />);
    await screen.findByRole("heading", { name: "模型派发" });
    fireEvent.change(await screen.findByLabelText("选择账号"), { target: { value: "user-1" } });
    fireEvent.click(await screen.findByLabelText("视频 Fast"));
    fireEvent.click(screen.getByRole("button", { name: "保存派发" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
        "/api/v1/admin/users/user-1/models",
        expect.objectContaining({ method: "PUT", body: JSON.stringify({ model_ids: ["image-pro", "video-fast"] }) }),
    ));
    expect(await screen.findByText("派发已保存")).toBeVisible();
});
