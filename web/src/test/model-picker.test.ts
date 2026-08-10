import { expect, it } from "vitest";
import { modelSupportsOperation, parameterControls } from "@/components/model-picker";

it("derives capabilities from the catalog even when a model name is misleading", () => {
    const model = { model_id: "banana-video", service_id: "s", display_name: "Definitely An Image Model", operations: ["video.image_to_video" as const], input_media: ["image" as const], requires_asset_kind: "portrait" as const, parameter_schema: {} };
    expect(modelSupportsOperation(model, "video.image_to_video")).toBe(true);
    expect(modelSupportsOperation(model, "image.generate")).toBe(false);
});

it("renders only the safe local parameter-schema subset", () => {
    const controls = parameterControls({ steps: { type: "integer", minimum: 1, maximum: 8, default: 4, script: "alert(1)" }, evil: { type: "object", component: "<img>" } });
    expect(controls).toEqual([{ name: "steps", type: "integer", minimum: 1, maximum: 8, default: 4 }]);
});
