import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ModelEditor } from "@/components/admin/model-editor";
import type { AdminLogicalModel } from "@/api/admin";

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

it("offers separated image and video templates without text or audio operations", () => {
    render(<ModelEditor model={null} onSave={vi.fn()} onSaved={vi.fn()} />);
    const template = screen.getByLabelText("能力模板");
    expect(template).toHaveTextContent("图像生成");
    expect(template).toHaveTextContent("多参考图编辑");
    expect(template).toHaveTextContent("视频生成");
    expect(template).not.toHaveTextContent(/文本|音频/);
    fireEvent.change(template, { target: { value: "chiyun_image_edit" } });
    expect(screen.getByText("image.edit")).toBeVisible();
});
