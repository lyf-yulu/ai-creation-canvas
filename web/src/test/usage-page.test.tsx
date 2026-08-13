import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import UsagePage from "@/pages/usage";
import { useSessionStore } from "@/stores/portal/use-session-store";

const ownerUsage = {
    summary: { successful_jobs: 1, image_count: 1, video_seconds: 5, total_cost_fen: 245 },
    jobs: [{ operation: "video.generate", status: "succeeded", video_seconds: 5, image_count: 1, video_price_fen: 25, image_price_fen: 120, cost_fen: 245, charged_at: "2026-08-13T00:00:00Z" }],
};

beforeEach(() => {
    useSessionStore.setState({
        session: { user_id: "user-1", username: "普通用户", role: "user", must_change_password: false },
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

it("shows an owner's charged total without exposing administrator rate controls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify(ownerUsage), { status: 200, headers: { "content-type": "application/json" } }));

    render(<UsagePage />);

    expect((await screen.findAllByText("¥2.45")).length).toBeGreaterThan(0);
    expect(screen.getByText("已完成任务")).toBeVisible();
    expect(screen.getByText("video.generate")).toBeVisible();
    expect(screen.queryByRole("button", { name: "保存价格" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/usage", expect.any(Object));
});

it("converts an administrator's decimal-yuan rates to integer fen before saving", async () => {
    useSessionStore.setState({ session: { user_id: "admin-1", username: "管理员", role: "admin", must_change_password: false } });
    const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify(ownerUsage), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ summary: ownerUsage.summary, users: [{ user_id: "user-1", summary: ownerUsage.summary }], jobs: ownerUsage.jobs }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ video_price_fen: 10, image_price_fen: 100 }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ video_price_fen: 25, image_price_fen: 120 }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<UsagePage />);

    await screen.findByLabelText("每秒视频价格（元）");
    fireEvent.change(screen.getByLabelText("每秒视频价格（元）"), { target: { value: "0.25" } });
    fireEvent.change(screen.getByLabelText("每张图片价格（元）"), { target: { value: "1.20" } });
    fireEvent.click(screen.getByRole("button", { name: "保存价格" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/admin/usage/rates", expect.objectContaining({ method: "PUT", body: JSON.stringify({ video_price_fen: 25, image_price_fen: 120 }) })));
});

it("shows each user's aggregate usage and every charged job to an administrator", async () => {
    useSessionStore.setState({ session: { user_id: "admin-1", username: "管理员", role: "admin", must_change_password: false } });
    vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify(ownerUsage), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(
            new Response(JSON.stringify({ summary: ownerUsage.summary, users: [{ user_id: "user-1", summary: ownerUsage.summary }], jobs: [{ ...ownerUsage.jobs[0], user_id: "user-1" }] }), { status: 200, headers: { "content-type": "application/json" } }),
        )
        .mockResolvedValueOnce(new Response(JSON.stringify({ video_price_fen: 10, image_price_fen: 100 }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<UsagePage />);

    expect(await screen.findByText("user-1")).toBeVisible();
    expect(screen.getByText("¥2.45 · 1 个任务")).toBeVisible();
    expect(screen.getByText("user-1 · video.generate")).toBeVisible();
});

it("keeps the administrator loading error visible when the owner request finishes later", async () => {
    useSessionStore.setState({ session: { user_id: "admin-1", username: "管理员", role: "admin", must_change_password: false } });
    let resolveOwner: (response: Response) => void;
    vi.spyOn(globalThis, "fetch")
        .mockImplementationOnce(
            () =>
                new Promise<Response>((resolve) => {
                    resolveOwner = resolve;
                }),
        )
        .mockResolvedValueOnce(new Response(JSON.stringify({ code: "internal_error" }), { status: 500, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ video_price_fen: 10, image_price_fen: 100 }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<UsagePage />);

    await screen.findByRole("alert");
    resolveOwner!(new Response(JSON.stringify(ownerUsage), { status: 200, headers: { "content-type": "application/json" } }));
    await screen.findAllByText("¥2.45");
    expect(screen.getByRole("alert")).toBeVisible();
});

it("restores the saved prices after an administrator save failure", async () => {
    useSessionStore.setState({ session: { user_id: "admin-1", username: "管理员", role: "admin", must_change_password: false } });
    const alertMock = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify(ownerUsage), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ summary: ownerUsage.summary, users: [], jobs: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ video_price_fen: 10, image_price_fen: 100 }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ code: "internal_error" }), { status: 500, headers: { "content-type": "application/json" } }));

    render(<UsagePage />);

    const videoInput = await screen.findByLabelText("每秒视频价格（元）");
    const imageInput = screen.getByLabelText("每张图片价格（元）");
    fireEvent.change(videoInput, { target: { value: "0.25" } });
    fireEvent.change(imageInput, { target: { value: "1.20" } });
    fireEvent.click(screen.getByRole("button", { name: "保存价格" }));

    await waitFor(() => expect(alertMock).toHaveBeenCalledWith("价格未保存，请重试。"));
    expect(videoInput).toHaveValue("0.10");
    expect(imageInput).toHaveValue("1.00");
});
