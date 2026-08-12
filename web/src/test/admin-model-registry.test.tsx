import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import AdminModelsPage from "@/pages/admin/models";


afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("creates a governed provider and model without exposing an API key control", async () => {
    const users = [{ user_id: "user-1", username: "canvas-user", display_name: "普通用户", role: "user", enabled: true, must_change_password: false, model_ids: [], created_at: 1, updated_at: 1 }];
    const provider = { provider_id: "chiyun", display_name: "Chiyun", adapter_type: "chiyun_openai_images", base_url: "https://chiyun.example", credential_ref: "chiyun-primary", enabled: true, revision: 1, credential_available: true };
    const definition = { model_id: "chiyun-gpt-image-2", provider_id: "chiyun", display_name: "GPT Image 2", introduction: "多参考图编辑", modality: "image", operations: ["image.edit"], enabled: true, revision: 1 };
    const fetchMock = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ users }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ models: [], diagnostics: [] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ providers: [], models: [], templates: [{ template_id: "chiyun_gpt_image_edit_v1", title: "Chiyun GPT Image 图生图", modality: "image", operation: "image.edit" }] }), { status: 200, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify(provider), { status: 201, headers: { "content-type": "application/json" } }))
        .mockResolvedValueOnce(new Response(JSON.stringify(definition), { status: 201, headers: { "content-type": "application/json" } }));

    render(<AdminModelsPage />);
    expect(await screen.findByRole("heading", { name: "模型派发" })).toBeVisible();
    expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Provider ID"), { target: { value: "chiyun" } });
    fireEvent.change(screen.getByLabelText("Provider 名称"), { target: { value: "Chiyun" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://chiyun.example" } });
    fireEvent.change(screen.getByLabelText("凭据引用"), { target: { value: "chiyun-primary" } });
    fireEvent.click(screen.getByRole("button", { name: "创建 Provider" }));
    await screen.findByText("Provider 已创建");
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/v1/admin/model-registry/providers", expect.objectContaining({ method: "POST", body: JSON.stringify({ provider_id: "chiyun", display_name: "Chiyun", adapter_type: "chiyun_openai_images", base_url: "https://chiyun.example", credential_ref: "chiyun-primary", enabled: true }) }));

    fireEvent.change(screen.getByLabelText("模型 ID"), { target: { value: "chiyun-gpt-image-2" } });
    fireEvent.change(screen.getByLabelText("供应商模型名"), { target: { value: "gpt-image-2" } });
    fireEvent.change(screen.getByLabelText("模型显示名"), { target: { value: "GPT Image 2" } });
    fireEvent.change(screen.getByLabelText("模型介绍"), { target: { value: "多参考图编辑" } });
    fireEvent.click(screen.getByRole("button", { name: "创建模型" }));
    await screen.findByText("模型已创建");
    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(5, "/api/v1/admin/model-registry/models", expect.objectContaining({ method: "POST" })));
    const body = JSON.parse(String((fetchMock.mock.calls[4]?.[1] as RequestInit).body));
    expect(body).toMatchObject({ model_id: "chiyun-gpt-image-2", provider_id: "chiyun", provider_model_name: "gpt-image-2", template_id: "chiyun_gpt_image_edit_v1" });
    expect(body).not.toHaveProperty("api_key");
    expect(body).not.toHaveProperty("parameter_mappings");
});
