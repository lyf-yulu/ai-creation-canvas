import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { ProductShell } from "@/components/layout/product-shell";
import { useSessionStore } from "@/stores/portal/use-session-store";


afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps administrator controls out of the ordinary-user shell at wide zoom layouts", () => {
    useSessionStore.setState({ session: { user_id: "admin", username: "管理员", role: "admin", must_change_password: false }, environment: "local", loading: false, errorCode: null, logout: vi.fn() });
    const view = render(<MemoryRouter><ProductShell><div data-testid="role-content" className="media-surface">内容</div></ProductShell></MemoryRouter>);
    expect(screen.getAllByRole("link", { name: "账号管理" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "模型派发" })).toHaveLength(2);
    expect(screen.getByTestId("task-tray")).toHaveClass("fixed", "bottom-0");
    expect(screen.getByTestId("product-main")).not.toContainElement(screen.getByTestId("task-tray"));
    expect(screen.getByTestId("role-content")).toHaveClass("media-surface");

    useSessionStore.setState({ session: { user_id: "user", username: "普通用户", role: "user", must_change_password: false } });
    view.rerender(<MemoryRouter><ProductShell><div data-testid="role-content" className="media-surface">内容</div></ProductShell></MemoryRouter>);
    expect(screen.queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "模型派发" })).not.toBeInTheDocument();
});
