import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { AdminAssetLibraryContent } from "@/pages/admin/asset-library";
import type { AdminAssetLibrary, AdminAssetLibraryGroup } from "@/api/admin";


afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

const summary: AdminAssetLibrary = {
    enabled: true,
    import_configured: true,
    has_ark_access: true,
    has_tos_access: true,
    tos_bucket: "canvas-uploads",
    tos_region: "cn-beijing",
    project_name: "Seedance2.0",
    revision_digest: "abc123",
    default_group_id: "asset-grp-1",
};
const groups: AdminAssetLibraryGroup[] = [{ group_id: "asset-grp-1", name: "canvas-aigc-default" }];

it("shows has_* booleans and never renders any credential values", async () => {
    render(<AdminAssetLibraryContent fetchSummary={async () => summary} fetchGroups={async () => groups} onImport={vi.fn()} />);

    await waitFor(() => expect(screen.getAllByText("已配置").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText("canvas-uploads")).toBeInTheDocument();
    expect(screen.getByText("canvas-aigc-default · asset-grp-1")).toBeInTheDocument();
    expect(screen.queryByText(/access[_-]?key|secret|AKLT|Bearer|SK-/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/access[ _-]?key/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/凭据/i)).not.toBeInTheDocument();
});

it("imports a config file through the confirmation flow", async () => {
    const onImport = vi.fn(async () => ({ ...summary, tos_bucket: "rotated-bucket" }));
    const fetchGroups = vi.fn(async () => groups);

    render(<AdminAssetLibraryContent fetchSummary={async () => summary} fetchGroups={fetchGroups} onImport={onImport} />);
    await screen.findByText("canvas-uploads");

    fireEvent.change(screen.getByLabelText("选择资产库配置 JSON"), {
        target: { files: [new File([JSON.stringify({ version: 1 })], "asset-library.json", { type: "application/json" })] },
    });
    const submit = screen.getByRole("button", { name: "导入" });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByLabelText("确认覆盖服务端配置"));
    fireEvent.click(submit);

    await waitFor(() => expect(onImport).toHaveBeenCalledOnce());
    await screen.findByText("rotated-bucket");
});

it("surfaces import failures without echoing file contents", async () => {
    const onImport = vi.fn(async () => { throw new Error("bad json"); });

    render(<AdminAssetLibraryContent fetchSummary={async () => summary} fetchGroups={async () => groups} onImport={onImport} />);
    await screen.findByText("canvas-uploads");

    fireEvent.change(screen.getByLabelText("选择资产库配置 JSON"), {
        target: { files: [new File([JSON.stringify({ version: 1 })], "asset-library.json", { type: "application/json" })] },
    });
    fireEvent.click(screen.getByLabelText("确认覆盖服务端配置"));
    fireEvent.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText("导入失败：请检查 JSON 格式与文件权限。");
});
