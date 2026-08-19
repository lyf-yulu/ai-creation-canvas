import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { saveAs } from "file-saver";

import AdminLogsPage from "@/pages/admin/logs";

vi.mock("file-saver", () => ({ saveAs: vi.fn() }));

beforeEach(() => {
    // antd Select dropdowns mount an rc-resize-observer; jsdom has no ResizeObserver.
    vi.stubGlobal(
        "ResizeObserver",
        class {
            observe() {}
            unobserve() {}
            disconnect() {}
        },
    );
});

const filesPayload = {
    files: [
        { name: "server.log", size: 2048, mtime: 1756666666 },
        { name: "server.log.1", size: 1024, mtime: 1756666000 },
    ],
};
const contentPayload = {
    file: "server.log",
    lines: 500,
    window_total: 3,
    truncated: false,
    log_lines: [
        '2026-08-19T10:00:01,123456+0800 INFO uvicorn.access 127.0.0.1:54321 - "GET /api/v1/session HTTP/1.1" 200 OK',
        "2026-08-19T10:00:02,123456+0800 WARNING ai_creation_canvas.job_polling generation job polling failed transiently",
        "2026-08-19T10:00:03,123456+0800 ERROR ai_creation_canvas.app unhandled request failure: POST /api/v1/jobs",
    ],
};
const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const linesContaining = (fragment: string) => (content: string) => content.includes(fragment);

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

it("lists log files and renders the newest window lines", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const url = String(input);
        if (url === "/api/v1/admin/logs/files") return json(filesPayload);
        if (url.startsWith("/api/v1/admin/logs/content")) return json(contentPayload);
        throw new Error(`unexpected ${url}`);
    });

    render(<AdminLogsPage />);

    expect(await screen.findByRole("heading", { name: "后台日志" })).toBeVisible();
    expect(screen.getByLabelText("日志文件")).toBeVisible();
    expect(await screen.findByText(linesContaining("generation job polling failed transiently"))).toBeVisible();
    expect(screen.getByText(linesContaining("unhandled request failure: POST /api/v1/jobs"))).toBeVisible();
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/admin/logs/files", expect.anything());
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/admin/logs/content?file=server.log&lines=500", expect.anything());
});

it("applies the selected level and keyword to the content request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const url = String(input);
        if (url === "/api/v1/admin/logs/files") return json(filesPayload);
        if (url.startsWith("/api/v1/admin/logs/content")) return json(contentPayload);
        throw new Error(`unexpected ${url}`);
    });

    render(<AdminLogsPage />);
    await screen.findByText(linesContaining("generation job polling failed transiently"));

    fireEvent.mouseDown(screen.getByLabelText("级别"));
    fireEvent.click(await screen.findByText("错误"));
    await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith("/api/v1/admin/logs/content?file=server.log&lines=500&level=ERROR", expect.anything()),
    );

    fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "polling" } });
    fireEvent.click(screen.getByRole("button", { name: /搜\s*索/ }));
    await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/admin/logs/content?file=server.log&lines=500&level=ERROR&q=polling",
            expect.anything(),
        ),
    );
});

it("exports the selected log file through the attachment download", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const url = String(input);
        if (url === "/api/v1/admin/logs/files") return json(filesPayload);
        if (url.startsWith("/api/v1/admin/logs/content")) return json(contentPayload);
        if (url.startsWith("/api/v1/admin/logs/download")) {
            return new Response(new Blob(["log bytes"], { type: "text/plain" }), {
                status: 200,
                headers: { "content-disposition": 'attachment; filename="server.log"' },
            });
        }
        throw new Error(`unexpected ${url}`);
    });

    render(<AdminLogsPage />);
    await screen.findByText(linesContaining("unhandled request failure"));

    fireEvent.click(screen.getByRole("button", { name: /导\s*出/ }));

    await waitFor(() => expect(saveAs).toHaveBeenCalledWith(expect.any(Blob), "server.log"));
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/admin/logs/download?file=server.log", expect.anything());
});

it("refreshes files and content every five seconds while auto-refresh is on", async () => {
    // testing-library's waitFor cannot advance vitest fake timers, so drive
    // the timer loop explicitly with act + advanceTimersByTimeAsync.
    vi.useFakeTimers();
    try {
        const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
            const url = String(input);
            if (url === "/api/v1/admin/logs/files") return json(filesPayload);
            if (url.startsWith("/api/v1/admin/logs/content")) return json(contentPayload);
            throw new Error(`unexpected ${url}`);
        });

        render(<AdminLogsPage />);
        await act(async () => {
            await vi.advanceTimersByTimeAsync(0);
        });
        expect(fetchMock).toHaveBeenCalledTimes(2);

        fireEvent.click(screen.getByRole("switch"));
        await act(async () => {
            await vi.advanceTimersByTimeAsync(5000);
        });
        expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4);

        fireEvent.click(screen.getByRole("switch"));
        const calls = fetchMock.mock.calls.length;
        await act(async () => {
            await vi.advanceTimersByTimeAsync(15000);
        });
        expect(fetchMock).toHaveBeenCalledTimes(calls);
    } finally {
        vi.useRealTimers();
    }
});

it("shows an empty state when no log files exist yet", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ files: [] }));

    render(<AdminLogsPage />);

    expect(await screen.findByText("暂无日志文件")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("shows a retryable banner when the log file list fails to load", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ code: "internal_error" }), { status: 500, headers: { "content-type": "application/json" } }),
    );

    render(<AdminLogsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("日志加载失败");
});
