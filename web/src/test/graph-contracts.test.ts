import { describe, expect, it } from "vitest";

import {
    GRAPH_SCHEMA_VERSION,
    createGraphSubmissionSnapshot,
    type GraphMediaCollectionMetadata,
    type GraphModelMetadata,
    type GraphPromptMetadata,
    type GraphResultMetadata,
} from "@/features/graph/contracts";
import { normalizeCanvasProject, UnsupportedGraphSchemaError, type CanvasProjectInput } from "@/features/graph/normalize-project";
import { normalizeConnection } from "@/lib/canvas/canvas-node-geometry";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

const timestamp = "2026-08-11T01:02:03.000Z";

// Canonical edges are never allowed to lose their port identity after deserialization.
// @ts-expect-error legacy port-less edges belong at the normalization input boundary
const portlessCanonicalConnection: CanvasConnection = { id: "legacy", fromNodeId: "a", toNodeId: "b" };
void portlessCanonicalConnection;

function node(id: string, type: CanvasNodeData["type"], metadata: CanvasNodeData["metadata"] = {}): CanvasNodeData {
    return { id, type, title: id, position: { x: 0, y: 0 }, width: 240, height: 160, metadata };
}

function project(nodes: CanvasNodeData[], connections: CanvasProjectInput["connections"] = []): CanvasProjectInput {
    return {
        id: "project-1",
        title: "Legacy",
        createdAt: timestamp,
        updatedAt: timestamp,
        nodes,
        connections,
        chatSessions: [],
        activeChatId: null,
        backgroundMode: "lines",
        showImageInfo: false,
        viewport: { x: 0, y: 0, k: 1 },
    };
}

describe("graph contracts", () => {
    it("represents the four graph roles with bounded media, ports, and model parameters", () => {
        const prompt: GraphPromptMetadata = { schemaVersion: GRAPH_SCHEMA_VERSION, role: "prompt", text: "镜头向前", outputPortId: "prompt" };
        const collection: GraphMediaCollectionMetadata = {
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "media-collection",
            mediaType: "image",
            outputPortId: "media",
            items: [{ id: "item-1", assetId: "asset-1", displayName: "参考图.png", mimeType: "image/png", bytes: 42, width: 64, height: 32 }],
        };
        const model: GraphModelMetadata = {
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "model",
            modelId: "seedream-test",
            operation: "image.edit",
            inputPortIds: ["prompt", "reference_images"],
            outputPortId: "result",
            parameters: { count: 2, watermark: false, ratio: "16:9" },
        };
        const result: GraphResultMetadata = {
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "result",
            mediaType: "image",
            outputPortId: "media",
            assetId: "result-asset",
            jobId: "job-1",
        };

        expect([prompt.role, collection.role, model.role, result.role]).toEqual(["prompt", "media-collection", "model", "result"]);
        expect(collection.items[0]).toMatchObject({ assetId: "asset-1", mimeType: "image/png", bytes: 42 });
        expect(model.parameters).toEqual({ count: 2, watermark: false, ratio: "16:9" });
    });

    it("creates an immutable submission snapshot independent from mutable editor values", () => {
        const source = {
            prompt: "@图片1 向前移动",
            modelId: "seedance-test",
            operation: "video.generate",
            parameters: { duration: 5, generateAudio: true },
            inputs: [{ portId: "reference_images", mediaType: "image" as const, assetIds: ["asset-1", "asset-2"] }],
        };

        const snapshot = createGraphSubmissionSnapshot(source);
        source.prompt = "changed";
        source.parameters.duration = 10;
        source.inputs[0].assetIds.reverse();

        expect(snapshot).toEqual({
            schemaVersion: GRAPH_SCHEMA_VERSION,
            prompt: "@图片1 向前移动",
            modelId: "seedance-test",
            operation: "video.generate",
            parameters: { duration: 5, generateAudio: true },
            inputs: [{ portId: "reference_images", mediaType: "image", assetIds: ["asset-1", "asset-2"] }],
        });
        expect(Object.isFrozen(snapshot)).toBe(true);
        expect(Object.isFrozen(snapshot.parameters)).toBe(true);
        expect(Object.isFrozen(snapshot.inputs[0].assetIds)).toBe(true);
    });

    it("adds source and target port IDs when interactive legacy nodes are connected", () => {
        const nodes = [node("prompt", CanvasNodeType.Text), node("model", CanvasNodeType.Config), node("result", CanvasNodeType.Video)];

        expect(normalizeConnection("prompt", "model", nodes, "source")).toEqual({
            fromNodeId: "prompt",
            fromPortId: "prompt",
            toNodeId: "model",
            toPortId: "prompt",
        });
        expect(normalizeConnection("model", "result", nodes, "source")).toEqual({
            fromNodeId: "model",
            fromPortId: "result",
            toNodeId: "result",
            toPortId: "result",
        });
    });
});

describe("legacy graph normalization", () => {
    it("migrates built-in nodes and unambiguous named-port connections without changing identity or timestamps", () => {
        const legacy = project(
            [
                node("prompt-a", CanvasNodeType.Text, { content: "第一条" }),
                node("prompt-b", CanvasNodeType.Text, { prompt: "第二条" }),
                node("image", CanvasNodeType.Image, { storageKey: "owned/image.png", mimeType: "image/png", bytes: 99 }),
                node("video", CanvasNodeType.Video, { storageKey: "owned/video.mp4", mimeType: "video/mp4" }),
                node("model", CanvasNodeType.Config, { model: "seedance-test", params: { ratio: "16:9", duration: 5, nested: { unsafe: true } } }),
                node("output", CanvasNodeType.Video, { sourceJobId: "job-1", storageKey: "/api/v1/jobs/job-1/result" }),
            ],
            [
                { id: "prompt-edge", fromNodeId: "prompt-a", toNodeId: "model" },
                { id: "second-prompt", fromNodeId: "prompt-b", toNodeId: "model" },
                { id: "image-edge", fromNodeId: "image", toNodeId: "model" },
                { id: "video-edge", fromNodeId: "video", toNodeId: "model" },
                { id: "output-edge", fromNodeId: "model", toNodeId: "output" },
            ],
        );

        const normalized = normalizeCanvasProject(legacy);

        expect(normalized).not.toBe(legacy);
        expect(normalized).toMatchObject({ id: "project-1", createdAt: timestamp, updatedAt: timestamp, graphSchemaVersion: GRAPH_SCHEMA_VERSION });
        expect(normalized.nodes.map((item) => item.metadata?.graph?.role)).toEqual(["prompt", "prompt", "result", "result", "model", "result"]);
        expect(normalized.nodes[0].metadata?.graph).toMatchObject({ role: "prompt", text: "第一条", outputPortId: "prompt" });
        expect(normalized.nodes[4].metadata?.graph).toMatchObject({
            role: "model",
            modelId: "seedance-test",
            parameters: { ratio: "16:9", duration: 5 },
        });
        expect(normalized.connections).toEqual([
            { id: "prompt-edge", fromNodeId: "prompt-a", fromPortId: "prompt", toNodeId: "model", toPortId: "prompt" },
            { id: "image-edge", fromNodeId: "image", fromPortId: "media", toNodeId: "model", toPortId: "reference_images" },
            { id: "video-edge", fromNodeId: "video", fromPortId: "media", toNodeId: "model", toPortId: "reference_video" },
            { id: "output-edge", fromNodeId: "model", fromPortId: "result", toNodeId: "output", toPortId: "result" },
        ]);
    });

    it("rejects dangling, self, duplicate, ambiguous, and second-prompt edges deterministically", () => {
        const legacy = project(
            [node("prompt-a", CanvasNodeType.Text), node("prompt-b", CanvasNodeType.Text), node("model", CanvasNodeType.Config), node("image-a", CanvasNodeType.Image), node("image-b", CanvasNodeType.Image)],
            [
                { id: "keep-prompt", fromNodeId: "prompt-a", toNodeId: "model" },
                { id: "drop-second-prompt", fromNodeId: "prompt-b", toNodeId: "model" },
                { id: "keep-image", fromNodeId: "image-a", toNodeId: "model" },
                { id: "drop-duplicate", fromNodeId: "image-a", toNodeId: "model" },
                { id: "drop-self", fromNodeId: "model", toNodeId: "model" },
                { id: "drop-dangling", fromNodeId: "missing", toNodeId: "model" },
                { id: "drop-ambiguous", fromNodeId: "image-a", toNodeId: "image-b" },
            ],
        );

        const once = normalizeCanvasProject(legacy);
        const twice = normalizeCanvasProject(once);

        expect(once.connections.map((edge) => edge.id)).toEqual(["keep-prompt", "keep-image"]);
        expect(twice).toEqual(once);
    });

    it("preserves unknown plugin nodes and already-valid named ports", () => {
        const plugin = node("plugin", "example:processor", { content: "opaque" });
        const secondPlugin = node("plugin-2", "example:sink", { content: "opaque-2" });
        const prompt = node("prompt", CanvasNodeType.Text, { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "prompt", text: "hello", outputPortId: "prompt" } });
        const model = node("model", CanvasNodeType.Config, { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "model", modelId: "model", operation: "custom", inputPortIds: ["custom_input", "prompt"], outputPortId: "result", parameters: {} } });
        const source = project([plugin, secondPlugin, prompt, model], [
            { id: "plugin-plugin", fromNodeId: "plugin", fromPortId: "custom_output", toNodeId: "plugin-2", toPortId: "custom_input" },
            { id: "plugin-model", fromNodeId: "plugin", fromPortId: "custom_output", toNodeId: "model", toPortId: "custom_input" },
            { id: "plugin-reserved", fromNodeId: "plugin", fromPortId: "custom_output", toNodeId: "model", toPortId: "prompt" },
            { id: "builtin-plugin", fromNodeId: "prompt", fromPortId: "prompt", toNodeId: "plugin-2", toPortId: "custom_input" },
        ]);

        const normalized = normalizeCanvasProject(source);

        expect(normalized.nodes.find((item) => item.id === "plugin")).toEqual(plugin);
        expect(normalized.connections).toEqual([
            { id: "plugin-plugin", fromNodeId: "plugin", fromPortId: "custom_output", toNodeId: "plugin-2", toPortId: "custom_input" },
            { id: "plugin-model", fromNodeId: "plugin", fromPortId: "custom_output", toNodeId: "model", toPortId: "custom_input" },
            { id: "builtin-plugin", fromNodeId: "prompt", fromPortId: "prompt", toNodeId: "plugin-2", toPortId: "custom_input" },
        ]);
    });

    it("validates explicit built-in roles and ports before consuming a model prompt slot", () => {
        const prompt = node("prompt", CanvasNodeType.Text, { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "prompt", text: "hello", outputPortId: "prompt" } });
        const image = node("image", CanvasNodeType.Image, { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "result", mediaType: "image", outputPortId: "media" } });
        const model = node("model", CanvasNodeType.Config, { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "model", modelId: "model", operation: "image.edit", inputPortIds: ["prompt", "reference_images"], outputPortId: "result", parameters: {} } });
        const result = node("result", CanvasNodeType.Image, { graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "result", mediaType: "image", outputPortId: "media" } });
        const source = project([prompt, image, model, result], [
            { id: "invalid-prompt-role", fromNodeId: "image", fromPortId: "media", toNodeId: "model", toPortId: "prompt" },
            { id: "valid-prompt", fromNodeId: "prompt", fromPortId: "prompt", toNodeId: "model", toPortId: "prompt" },
            { id: "invalid-source-port", fromNodeId: "prompt", fromPortId: "media", toNodeId: "model", toPortId: "prompt" },
            { id: "invalid-model-input", fromNodeId: "image", fromPortId: "media", toNodeId: "model", toPortId: "first_frame" },
            { id: "partial-explicit-port", fromNodeId: "image", fromPortId: "media", toNodeId: "model" },
            { id: "valid-image", fromNodeId: "image", fromPortId: "media", toNodeId: "model", toPortId: "reference_images" },
            { id: "invalid-model-output", fromNodeId: "model", fromPortId: "media", toNodeId: "result", toPortId: "result" },
        ]);

        expect(normalizeCanvasProject(source).connections.map((edge) => edge.id)).toEqual(["valid-prompt", "valid-image"]);
    });

    it("rebuilds malformed current-version metadata from bounded legacy fields", () => {
        const malformed = node("image", CanvasNodeType.Image, {
            mimeType: "image/png",
            graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "media-collection", mediaType: "image" } as never,
        });

        const normalized = normalizeCanvasProject(project([malformed]));

        expect(normalized.nodes[0].metadata?.graph).toEqual({
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "result",
            mediaType: "image",
            outputPortId: "media",
        });
    });

    it("rejects a future graph schema without overwriting its opaque metadata", () => {
        const future = node("future", CanvasNodeType.Text, {
            content: "legacy fallback must not replace this",
            graph: { schemaVersion: GRAPH_SCHEMA_VERSION + 1, role: "future-role", opaque: { value: 42 } } as never,
        });
        const source = project([future]);

        expect(() => normalizeCanvasProject(source)).toThrow(UnsupportedGraphSchemaError);
        expect((source.nodes[0].metadata?.graph as unknown as { opaque: { value: number } }).opaque.value).toBe(42);
    });
});
