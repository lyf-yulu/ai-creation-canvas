import { expect, it } from "vitest";

import { getNodePorts } from "@/features/graph/connect";
import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import { normalizeCanvasProject } from "@/features/graph/normalize-project";
import { createComfyWorkflowNode } from "@/features/nodes/comfy-workflow";
import { nodeRegistry } from "@/features/nodes/registry";

it("registers one generic ComfyUI node without registering imported node types", () => {
    expect(nodeRegistry.getNode("comfy.workflow")).toMatchObject({
        title: "ComfyUI 工作流",
        version: 1,
        showInCreateMenu: true,
    });
    expect(nodeRegistry.getNode("MiniMaxH3ImageToVideo")).toBeUndefined();
});

it("preserves a selected template revision and leaves execution disabled", () => {
    const node = createComfyWorkflowNode({
        workflowId: "wf-1",
        revision: 2,
        title: "Core",
        inputs: [{ id: "prompt", accepts: "prompt" }],
        executionEnabled: false,
    });

    expect(node.metadata?.graph).toMatchObject({
        schemaVersion: GRAPH_SCHEMA_VERSION,
        role: "comfy-workflow",
        workflowId: "wf-1",
        workflowRevision: 2,
        inputPorts: [{ id: "prompt", accepts: "prompt" }],
        outputPortId: "result",
        executionEnabled: false,
    });
    expect(node.title).toBe("Core");
    expect(getNodePorts(node)).toEqual({
        sources: [expect.objectContaining({ portId: "result", valueType: "result" })],
        targets: [expect.objectContaining({ portId: "prompt", valueType: "prompt" })],
    });
});

it("normalizes ComfyUI workflow metadata without retaining non-project workflow fields", () => {
    const node = createComfyWorkflowNode({
        workflowId: "wf-1",
        revision: 2,
        title: "Core",
        inputs: [],
        executionEnabled: false,
    });
    const project = normalizeCanvasProject({
        id: "project",
        title: "Project",
        createdAt: "2026-08-16T00:00:00.000Z",
        updatedAt: "2026-08-16T00:00:00.000Z",
        nodes: [{ ...node, metadata: { ...node.metadata, graph: { ...node.metadata!.graph!, rawJson: { class_type: "KSampler" }, serviceUrl: "https://example.invalid", credential: "secret" } } }],
        connections: [],
        chatSessions: [],
        activeChatId: null,
        backgroundMode: "lines",
        showImageInfo: false,
        viewport: { x: 0, y: 0, k: 1 },
    });

    expect(project.nodes[0]?.metadata?.graph).toEqual({
        schemaVersion: GRAPH_SCHEMA_VERSION,
        role: "comfy-workflow",
        workflowId: "wf-1",
        workflowRevision: 2,
        inputPorts: [],
        outputPortId: "result",
        executionEnabled: false,
    });
});
