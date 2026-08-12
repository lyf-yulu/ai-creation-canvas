import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import AdminModelsPage from "@/pages/admin/models";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("creates a logical model without requesting legacy Provider credentials", async () => {
    const users = [{ user_id: "user-1", username: "canvas-user", display_name: "普通用户", role: "user", enabled: true, must_change_password: false, model_ids: [], created_at: 1, updated_at: 1 }];
    const definition = { model_id: "banana", display_name: "Nano Banana", introduction: "多参考图编辑", modality: "image", operation_contracts: [{ operation: "image.edit", input_ports: [], output_media_type: "image", parameter_schema: {}, parameter_mappings: {} }], enabled: false, archived_at: null, revision: 1 };
    const fetchMock = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ users }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [], diagnostics: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ pools: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify(definition), { status: 201, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ routes: [] }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<AdminModelsPage />);
    expect(await screen.findByRole("heading", { name: "模型与调用线路" })).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("model-registry"))).toBe(false);
    expect(screen.queryByLabelText(/API Key|Base URL|凭据引用/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText("模型 ID"), { target: { value: "banana" } });
    fireEvent.change(screen.getByLabelText("模型显示名"), { target: { value: "Nano Banana" } });
    fireEvent.change(screen.getByLabelText("模型介绍"), { target: { value: "多参考图编辑" } });
    fireEvent.change(screen.getByLabelText("能力模板"), { target: { value: "chiyun_image_edit" } });
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6)); // create response selects the model and loads its routes
    const createCall = fetchMock.mock.calls.find(([url]) => url === "/api/v1/admin/logical-models");
    expect(createCall?.[1]).toEqual(expect.objectContaining({ method: "POST" }));
    const body = JSON.parse(String((createCall?.[1] as RequestInit).body));
    expect(body).toMatchObject({ model_id: "banana", modality: "image", enabled: false });
    expect(body.operation_contracts[0].operation).toBe("image.edit");
    expect(JSON.stringify(body)).not.toMatch(/api_key|base_url|credential_ref/i);
});
