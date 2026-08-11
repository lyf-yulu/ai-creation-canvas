import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ModelSpec } from "@/api/contracts";
import { ModelCallNode } from "@/components/canvas/model-call-node";
import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import { CanvasNodeType } from "@/types/canvas";

const models: ModelSpec[] = [{
    model_id: "image", service_id: "ark", display_name: "Seedream", operations: ["image.generate"], input_media: ["text"],
    input_ports: [{ port_id: "prompt", media_type: "text", min_items: 1, max_items: 1 }],
    parameter_schema: { type: "object", properties: { quality: { type: "string", enum: ["standard", "high"], default: "standard" }, count: { type: "integer", minimum: 0, maximum: 4, default: 0 }, watermark: { type: "boolean", default: false } }, additionalProperties: false },
    parameter_mappings: { quality: "quality", count: "n", watermark: "watermark" },
}];

const node = { id: "model", type: CanvasNodeType.Config, title: "图片生成", position: { x: 0, y: 0 }, width: 320, height: 300, metadata: { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "model" as const, modelId: "image", operation: "image.generate", inputPorts: [{ id: "prompt", accepts: "prompt" as const }], outputPortId: "result", parameters: { quality: "standard", count: 0, watermark: false } } } };

describe("ModelCallNode", () => {
    it("renders declared parameters in-node and preserves exact values", () => {
        const onChange = vi.fn(); const onRun = vi.fn();
        render(<ModelCallNode node={node} models={models} onChange={onChange} onRun={onRun} />);
        expect(screen.getByText("提示词：1")).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText("count"), { target: { value: "2" } });
        expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ parameters: expect.objectContaining({ count: 2 }) }));
        fireEvent.click(screen.getByRole("button", { name: "运行模型" }));
        expect(onRun).toHaveBeenCalledTimes(1);
    });
});
