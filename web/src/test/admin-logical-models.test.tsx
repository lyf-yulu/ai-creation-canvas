import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ModelEditor } from "@/components/admin/model-editor";
import type { AdminLogicalModel } from "@/api/admin";
import frozenProfiles from "../../../tests/fixtures/acceptance-model-profiles.json";
import { ADMIN_MODEL_TEMPLATES, callingPresetsForModel } from "@/components/admin/model-templates";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const image: AdminLogicalModel = {
    model_id: "banana", display_name: "Banana", introduction: "Image editor", modality: "image",
    operation_contracts: [{ operation: "image.edit", input_ports: [{ port_id: "prompt", media_type: "text", min_items: 1, max_items: 1 }, { port_id: "reference_images", media_type: "image", min_items: 1, max_items: 10 }], output_media_type: "image", parameter_schema: { type: "object", properties: {}, additionalProperties: false }, parameter_mappings: {} }],
    enabled: true, archived_at: null, revision: 7,
};

it("edits the selected logical model with its revision and ignores a late prior response", async () => {
    let resolveFirst!: (value: AdminLogicalModel) => void;
    const save = vi.fn().mockImplementationOnce(() => new Promise<AdminLogicalModel>((resolve) => { resolveFirst = resolve; }));
    const saved = vi.fn();
    const { rerender } = render(<ModelEditor model={image} onSave={save} onSaved={saved} />);
    fireEvent.change(screen.getByLabelText("模型显示名"), { target: { value: "Banana 2" } });
    fireEvent.change(screen.getByLabelText("模型介绍"), { target: { value: "Changed" } });
    const saveButton = screen.getByRole("button", { name: "保存模型" });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ model_id: "banana", revision: 7, display_name: "Banana 2" }));

    const video = { ...image, model_id: "seedance", display_name: "Seedance", modality: "video" as const, revision: 3 };
    rerender(<ModelEditor model={video} onSave={save} onSaved={saved} />);
    resolveFirst({ ...image, display_name: "Banana 2", revision: 8 });
    await waitFor(() => expect(saved).not.toHaveBeenCalled());
    expect(screen.getByLabelText("模型显示名")).toHaveValue("Seedance");
});

it("offers exactly two capability templates and separates the concrete model protocol", () => {
    render(<ModelEditor model={null} onSave={vi.fn()} onSaved={vi.fn()} />);
    const capability = screen.getByLabelText("能力模板");
    expect(capability.querySelectorAll("option")).toHaveLength(2);
    expect(capability).toHaveTextContent("多参生图");
    expect(capability).toHaveTextContent("多参生视频");
    expect(capability).not.toHaveTextContent(/Ark|Chiyun|T8Star/);
    const protocol = screen.getByLabelText("模型类型");
    expect(protocol).toHaveTextContent("Seedream");
    expect(protocol).toHaveTextContent("Banana");
    expect(protocol).toHaveTextContent("GPT-Image2");
    fireEvent.change(protocol, { target: { value: "banana" } });
    expect(screen.getByText("image.edit")).toBeVisible();
    fireEvent.change(capability, { target: { value: "multi_video" } });
    expect(screen.getByLabelText("模型类型").querySelectorAll("option")).toHaveLength(1);
    expect(screen.getByLabelText("模型类型")).toHaveTextContent("Seedance");
    expect(screen.getByText("video.generate")).toBeVisible();
});

it("does not publish a pending save result or error after unmount", async () => {
    let resolveSave!: (value: AdminLogicalModel) => void;
    const saved = vi.fn();
    const save = vi.fn(() => new Promise<AdminLogicalModel>((resolve) => { resolveSave = resolve; }));
    const { unmount } = render(<ModelEditor model={image} onSave={save} onSaved={saved} />);
    fireEvent.change(screen.getByLabelText("模型显示名"), { target: { value: "Pending" } });
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    unmount();
    resolveSave({ ...image, display_name: "Pending", revision: 8 });
    await Promise.resolve();
    expect(saved).not.toHaveBeenCalled();
});

it("keeps all four admin templates exactly aligned with the frozen server acceptance profiles", () => {
    const fixture = frozenProfiles.profiles as Record<string, { provider_model_name: string; contract: unknown }>;
    const profileIds = { banana: "banana", gpt_image2: "gpt-image2", seedream: "seedream", seedance: "seedance" } as const;
    for (const template of ADMIN_MODEL_TEMPLATES) {
        const expected = fixture[profileIds[template.id]];
        expect(template.contract).toEqual(expected.contract);
        const model = { ...image, operation_contracts: [template.contract] };
        expect(callingPresetsForModel(model)[0].providerModelName).toBe(expected.provider_model_name);
    }
});
