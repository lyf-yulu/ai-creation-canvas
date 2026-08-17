import { MemoryRouter, Route, Routes } from "react-router-dom";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import RegisterPage from "@/pages/auth/register";


function renderPage() {
    return render(
        <MemoryRouter initialEntries={["/register"]}>
            <Routes>
                <Route path="/register" element={<RegisterPage />} />
                <Route path="/login" element={<div>login-page</div>} />
            </Routes>
        </MemoryRouter>,
    );
}

function fillForm() {
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "newcomer" } });
    fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "新同事" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "correct-horse-battery" } });
}

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

it("rejects mismatched passwords before any request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    renderPage();
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "newcomer" } });
    fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "新同事" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "another-correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "提交注册" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("两次输入的密码不一致。");
    expect(fetchMock).not.toHaveBeenCalled();
});

it("submits the registration and shows the pending-approval panel", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ registered: true }), { status: 201, headers: { "content-type": "application/json" } }));

    renderPage();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "提交注册" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/register",
        expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ username: "newcomer", display_name: "新同事", password: "correct-horse-battery" }),
        }),
    ));
    expect(await screen.findByRole("heading", { name: "注册已提交" })).toBeVisible();
    expect(screen.getByText("注册已提交，请等待管理员审核后登录。")).toBeVisible();
});

it("reports a taken username from the server response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
        code: "USERNAME_TAKEN", message: "用户名已被占用。", retryable: false, request_id: "req-taken", phase: "request",
    }), { status: 409, headers: { "content-type": "application/json" } }));

    renderPage();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "提交注册" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("用户名已被占用。");
    expect(screen.queryByRole("heading", { name: "注册已提交" })).not.toBeInTheDocument();
});
