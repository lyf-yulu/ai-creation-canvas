import { expect, it } from "vitest";

import { listNodes, createNodeRegistry } from "@/features/nodes/registry";
import { createWorkflowRegistry, getWorkflow } from "@/features/workflows/registry";
import { portraitVideoWorkflow } from "@/features/workflows/portrait-video";

it("adds a node and workflow only through isolated registration", () => {
    const nodes = createNodeRegistry();
    const workflows = createWorkflowRegistry();
    nodes.registerNode({ id: "test.note", version: 1, title: "测试", inputs: [], outputs: ["text"], createMetadata: () => ({}), render: () => null });
    workflows.registerWorkflow({ id: "test.flow", version: 1, run: async () => ({ jobId: "job-1" }) });

    expect(nodes.listNodes().map((node) => node.id)).toEqual(["test.note"]);
    expect(workflows.getWorkflow("test.flow")?.id).toBe("test.flow");
    expect(listNodes().some((node) => node.id === "test.note")).toBe(false);
    expect(getWorkflow("test.flow")).toBeUndefined();
});

it("rejects duplicate node and workflow IDs without replacing the original", () => {
    const nodes = createNodeRegistry();
    const workflows = createWorkflowRegistry();
    const node = { id: "test.note", version: 1, title: "原始", inputs: [], outputs: [], createMetadata: () => ({}), render: () => null };
    nodes.registerNode(node);
    workflows.registerWorkflow({ id: "test.flow", version: 1, run: async () => ({ jobId: "job-1" }) });

    expect(() => nodes.registerNode({ ...node, title: "替换" })).toThrow("duplicate node: test.note");
    expect(() => workflows.registerWorkflow({ id: "test.flow", version: 2, run: async () => ({ jobId: "job-2" }) })).toThrow("duplicate workflow: test.flow");
    expect(nodes.listNodes()[0]?.title).toBe("原始");
});

it("does not expose mutable registry collections and returns undefined for unknown workflows", () => {
    const nodes = createNodeRegistry();
    nodes.registerNode({ id: "test.note", version: 1, title: "测试", inputs: [], outputs: [], createMetadata: () => ({}), render: () => null });
    const listed = nodes.listNodes();
    expect(() => (listed as unknown[]).pop()).toThrow();

    expect(nodes.listNodes()).toHaveLength(1);
    expect(getWorkflow("unknown.workflow")).toBeUndefined();
});

it("runs portrait video as upload, active asset, then generic image-to-video submission", async () => {
    const calls: string[] = [];
    const asset = await portraitVideoWorkflow.run({
        file: new File(["image"], "portrait.png", { type: "image/png" }),
        modelId: "video-model-a",
        serviceId: "video-service-a",
        prompt: "walk forward",
        params: { seconds: 5 },
        idempotencyKey: "portrait-1",
        uploadAsset: async (file, kind) => {
            calls.push(`upload:${file.name}:${kind}`);
            return { id: "asset-1", kind: "portrait", status: "processing", mime_type: "image/png" };
        },
        fetchAsset: async () => {
            calls.push("asset");
            return { id: "asset-1", kind: "portrait", status: "active", mime_type: "image/png" };
        },
        submitJob: async (request) => {
            calls.push(`submit:${request.operation}:${request.model_id}`);
            expect(request.asset_ids).toEqual(["asset-1"]);
            expect(request.service_id).toBe("video-service-a");
            return { jobId: "job-1" };
        },
        sleep: async () => undefined,
    });

    expect(calls).toEqual(["upload:portrait.png:portrait", "asset", "submit:video.image_to_video:video-model-a"]);
    expect(asset).toEqual({ jobId: "job-1", assetId: "asset-1" });
});

it("stops portrait workflow when the asset fails or remains pending past its timeout", async () => {
    const base = {
        file: new File(["image"], "portrait.png", { type: "image/png" }),
        modelId: "video-model-a",
        prompt: "walk forward",
        params: {},
        idempotencyKey: "portrait-1",
        uploadAsset: async () => ({ id: "asset-1", kind: "portrait" as const, status: "processing" as const, mime_type: "image/png" }),
        submitJob: async () => ({ jobId: "job-1" }),
        sleep: async () => undefined,
    };
    await expect(portraitVideoWorkflow.run({ ...base, fetchAsset: async () => ({ id: "asset-1", kind: "portrait", status: "failed", mime_type: "image/png" }) })).rejects.toThrow("asset asset-1 failed");
    await expect(portraitVideoWorkflow.run({ ...base, fetchAsset: async () => ({ id: "asset-1", kind: "portrait", status: "processing", mime_type: "image/png" }), pollIntervalMs: 1, maxWaitMs: 1 })).rejects.toThrow("asset asset-1 timed out");
});
