import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "antd";

import { ProductShell } from "@/components/layout/product-shell";
import { useSessionStore } from "@/stores/portal/use-session-store";


beforeEach(() => {
    useSessionStore.setState({
        session: { user_id: "user-a", username: "普通用户 A", role: "user", must_change_password: false },
        environment: "local",
        loading: false,
        errorCode: null,
        logout: vi.fn(async () => useSessionStore.getState().clearSession()),
    });
});

afterEach(() => cleanup());

it("opens the change-password dialog from the sidebar footer and submits", async () => {
    const changePassword = vi.fn(async () => ({ user_id: "user-a", username: "普通用户 A", role: "user" as const, must_change_password: false }));
    useSessionStore.setState({ changePassword });

    render(<MemoryRouter><App><ProductShell><div>内容区域</div></ProductShell></App></MemoryRouter>);

    fireEvent.click(within(screen.getByRole("complementary", { name: "侧边栏" })).getByRole("button", { name: "修改密码" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("当前密码"), { target: { value: "old-password" } });
    fireEvent.change(within(dialog).getByLabelText("新密码"), { target: { value: "new-password-123456" } });
    fireEvent.change(within(dialog).getByLabelText("确认新密码"), { target: { value: "new-password-123456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(changePassword).toHaveBeenCalledWith("old-password", "new-password-123456"));
    expect(await screen.findByText("密码已修改")).toBeInTheDocument();
});

it("keeps the fixed task tray outside the scrollable content", () => {
    render(<MemoryRouter><ProductShell><div>内容区域</div></ProductShell></MemoryRouter>);

    expect(screen.getByTestId("product-main")).toHaveClass("pb-[var(--task-tray-height)]");
    expect(screen.getByTestId("task-tray")).toHaveClass("fixed", "bottom-0");
    expect(screen.getByTestId("product-main")).not.toContainElement(screen.getByTestId("task-tray"));
});

it("shows only released ordinary-user destinations", () => {
    render(<MemoryRouter><ProductShell><div>内容区域</div></ProductShell></MemoryRouter>);

    expect(screen.getAllByRole("link", { name: "项目" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "项目" }).every((link) => link.getAttribute("href") === "/canvas")).toBe(true);
    expect(screen.getAllByRole("link", { name: "资产" }).every((link) => link.getAttribute("href") === "/assets")).toBe(true);
    expect(screen.getAllByRole("link", { name: "任务" }).every((link) => link.getAttribute("href") === "/tasks")).toBe(true);
    expect(screen.queryByRole("link", { name: "管理员" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Skill" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "退出登录" })).toHaveLength(2);
});

it("returns to the active canvas after visiting another product page and resets for another user", () => {
    render(<MemoryRouter initialEntries={["/canvas/project-a"]}><ProductShell><div>内容区域</div></ProductShell></MemoryRouter>);

    expect(screen.getAllByRole("link", { name: "项目" }).every((link) => link.getAttribute("href") === "/canvas/project-a")).toBe(true);
    fireEvent.click(screen.getAllByRole("link", { name: "资产" })[0]);
    expect(screen.getAllByRole("link", { name: "项目" }).every((link) => link.getAttribute("href") === "/canvas/project-a")).toBe(true);

    act(() => useSessionStore.setState({ session: { user_id: "user-b", username: "普通用户 B", role: "user", must_change_password: false } }));
    expect(screen.getAllByRole("link", { name: "项目" }).every((link) => link.getAttribute("href") === "/canvas")).toBe(true);
});

it("does not adopt the previous user's canvas when the session changes on that route", () => {
    render(<MemoryRouter initialEntries={["/canvas/project-a"]}><ProductShell><div>内容区域</div></ProductShell></MemoryRouter>);
    expect(screen.getAllByRole("link", { name: "项目" }).every((link) => link.getAttribute("href") === "/canvas/project-a")).toBe(true);

    act(() => useSessionStore.setState({ session: { user_id: "user-b", username: "普通用户 B", role: "user", must_change_password: false } }));

    expect(screen.getAllByRole("link", { name: "项目" }).every((link) => link.getAttribute("href") === "/canvas")).toBe(true);
});
