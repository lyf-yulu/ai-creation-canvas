import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ModelRouteEditor } from "@/components/admin/model-route-editor";
import type { AdminCredentialPool, AdminLogicalModel } from "@/api/admin";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const model: AdminLogicalModel = {
    model_id: "banana", display_name: "Nano Banana", introduction: "Edit", modality: "image",
    operation_contracts: [{ operation: "image.edit", input_ports: [{ port_id: "prompt", media_type: "text", min_items: 1, max_items: 1 }, { port_id: "reference_images", media_type: "image", min_items: 1, max_items: 10 }], output_media_type: "image", parameter_schema: { type: "object", properties: { size: { type: "string", enum: ["auto", "1024x1024", "1024x1536", "1536x1024"], default: "auto" }, output_count: { type: "integer", minimum: 1, maximum: 4, default: 1 } }, required: ["size", "output_count"], additionalProperties: false }, parameter_mappings: { size: "size", output_count: "n" } }],
    enabled: true, archived_at: null, revision: 1,
};
const pools: AdminCredentialPool[] = [
    { pool_id: "t8-gemini", provider_id: "t8star", group: "gemini", allowed_families: ["nano-banana"], revision_digest: "a".repeat(64), key_count: 2, total_capacity: 4, capacity_status: "available", available_count: 2, busy_count: 0, circuit_status: "unsupported", circuit_open_count: null },
    { pool_id: "t8-cc", provider_id: "t8star", group: "cc", allowed_families: ["claude"], revision_digest: "b".repeat(64), key_count: 1, total_capacity: 1, capacity_status: "available", available_count: 1, busy_count: 0, circuit_status: "unsupported", circuit_open_count: null },
    { pool_id: "ark-image", provider_id: "ark", group: "official", allowed_families: ["seedream"], revision_digest: "c".repeat(64), key_count: 1, total_capacity: 2, capacity_status: "unavailable", available_count: null, busy_count: null, circuit_status: "unsupported", circuit_open_count: null },
];

it("filters pools by exact provider and family and clears an invalid selection", () => {
    render(<ModelRouteEditor model={model} route={null} pools={pools} onSave={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("线路模板"), { target: { value: "chiyun_image_edit" } });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "t8star" } });
    const select = screen.getByLabelText("凭据池");
    expect(select).toHaveTextContent("t8-gemini");
    expect(select).not.toHaveTextContent("t8-cc");
    fireEvent.change(select, { target: { value: "t8-gemini" } });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "ark" } });
    expect(select).toHaveValue("");
});

it("locks the capability family to the trusted route template so cc cannot be selected", () => {
    render(<ModelRouteEditor model={model} route={null} pools={pools} onSave={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText("线路模板")).toHaveTextContent("多参考图编辑 · Ark");
    expect(screen.getByLabelText("线路模板")).toHaveTextContent("多参考图编辑 · Chiyun");
    fireEvent.change(screen.getByLabelText("线路模板"), { target: { value: "chiyun_image_edit" } });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "t8star" } });
    expect(screen.getByLabelText("模型族")).toHaveTextContent("nano-banana");
    expect(screen.getByLabelText("模型族").tagName).not.toBe("INPUT");
    expect(screen.getByLabelText("凭据池")).not.toHaveTextContent("t8-cc");
});

it("renders safe pool counts and never exposes deployment credentials", () => {
    const { container } = render(<ModelRouteEditor model={model} route={null} pools={pools} onSave={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByText(/2 把凭据/)).toBeVisible();
    expect(screen.getByText(/可用 2/)).toBeVisible();
    expect(container).not.toHaveTextContent(/fixture-secret|key-1|api key|base url|凭据引用/i);
    expect(container.querySelector('[name="api_key"]')).toBeNull();
});

it("makes a mismatched historical family visibly read-only instead of submitting a misleading template", () => {
    const legacy = { route_id: "legacy-cc", model_id: "banana", provider_id: "t8star", provider_model_name: "legacy", adapter_type: "chiyun_openai_images" as const, credential_pool_ref: "t8-cc", family: "cc", operation_contracts: model.operation_contracts, priority: 1, max_concurrency: 1, enabled: false, archived_at: null, revision: 1 };
    render(<ModelRouteEditor model={model} route={legacy} pools={pools} onSave={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText("模型族")).toHaveTextContent("cc");
    expect(screen.getByRole("alert")).toHaveTextContent("只读");
    expect(screen.getByRole("button", { name: "保存线路" })).toBeDisabled();
});
