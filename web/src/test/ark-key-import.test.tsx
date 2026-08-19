import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ArkKeyImport } from "@/components/admin/ark-key-import";


afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

it("uploads the selected Ark key file without reading it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ configured: true, has_key: true }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    render(<ArkKeyImport />);
    fireEvent.change(screen.getByLabelText("选择 Key JSON"), {
        target: { files: [new File([JSON.stringify({ version: 1, api_key: "real-ark-key-12345" })], "ark-key.json", { type: "application/json" })] },
    });
    fireEvent.click(screen.getByLabelText("确认替换现有方舟 Key"));
    fireEvent.click(screen.getByRole("button", { name: "导入并替换方舟 Key" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/admin/ark-key/import",
        expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    ));
    expect(await screen.findByText("方舟 Key 已导入，新任务立即生效。")).toBeVisible();
});

it("shows a failure hint when the import is rejected", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ code: "ARK_KEY_INVALID", message: "invalid", retryable: false, request_id: "r", phase: "request" }), { status: 400, headers: { "content-type": "application/json" } }),
    );
    render(<ArkKeyImport />);
    fireEvent.change(screen.getByLabelText("选择 Key JSON"), {
        target: { files: [new File(["{}"], "ark-key.json", { type: "application/json" })] },
    });
    fireEvent.click(screen.getByLabelText("确认替换现有方舟 Key"));
    fireEvent.click(screen.getByRole("button", { name: "导入并替换方舟 Key" }));
    expect(await screen.findByText(/导入失败/)).toBeVisible();
});
