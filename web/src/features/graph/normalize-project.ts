import { GRAPH_SCHEMA_VERSION, type CanvasGraphNodeMetadata, type GraphMediaType, type GraphParameterValue } from "@/features/graph/contracts";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type CanvasNodeMetadata } from "@/types/canvas";

export class UnsupportedGraphSchemaError extends Error {
    constructor(version: unknown) {
        super(`Unsupported canvas graph schema version: ${version}`);
        this.name = "UnsupportedGraphSchemaError";
    }
}

export type CanvasConnectionInput = Omit<CanvasConnection, "fromPortId" | "toPortId"> & {
    fromPortId?: string;
    toPortId?: string;
};

export type CanvasNodeInput = Omit<CanvasNodeData, "metadata"> & {
    metadata?: Omit<CanvasNodeMetadata, "graph"> & { graph?: unknown };
};

export type CanvasProjectInput = Omit<CanvasProject, "graphSchemaVersion" | "nodes" | "connections"> & {
    graphSchemaVersion?: number;
    nodes: CanvasNodeInput[];
    connections: CanvasConnectionInput[];
};

export function normalizeCanvasProject(project: CanvasProjectInput): CanvasProject {
    const cloned = cloneJsonValue(project) as CanvasProjectInput;
    assertSupportedSchema(cloned);
    const nodes = cloned.nodes.map(normalizeNode);
    return {
        ...cloned,
        graphSchemaVersion: GRAPH_SCHEMA_VERSION,
        nodes,
        connections: normalizeConnections(cloned.connections, nodes),
        chatSessions: cloned.chatSessions,
        viewport: cloned.viewport,
    };
}

export function normalizeCanvasProjectBaselineSnapshot(snapshot: string, fallbackProject: CanvasProjectInput): string {
    const parsed = JSON.parse(snapshot) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new TypeError("Invalid canvas project baseline snapshot");
    const normalized = normalizeCanvasProject({ ...fallbackProject, ...(parsed as Partial<CanvasProjectInput>), updatedAt: fallbackProject.updatedAt });
    const { updatedAt: _timestamp, ...content } = normalized;
    return canonicalJson(content);
}

function assertSupportedSchema(project: CanvasProjectInput) {
    if (Object.prototype.hasOwnProperty.call(project, "graphSchemaVersion")) assertCurrentSchemaVersion(project.graphSchemaVersion);
    for (const node of project.nodes) {
        const graph = node.metadata?.graph;
        if (!graph || typeof graph !== "object") continue;
        if (Object.prototype.hasOwnProperty.call(graph, "schemaVersion")) assertCurrentSchemaVersion((graph as { schemaVersion?: unknown }).schemaVersion);
    }
}

function assertCurrentSchemaVersion(version: unknown) {
    if (typeof version !== "number" || !Number.isInteger(version) || version !== GRAPH_SCHEMA_VERSION) throw new UnsupportedGraphSchemaError(version);
}

function cloneJsonValue(value: unknown, depth = 0, budget = { remaining: 100_000 }): unknown {
    if (depth > 64 || budget.remaining-- <= 0) throw new TypeError("Canvas project JSON exceeds clone bounds");
    if (value === null || value === undefined || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new TypeError("Canvas project JSON contains a non-finite number");
        return value;
    }
    if (typeof value !== "object") throw new TypeError("Canvas project contains a non-JSON value");
    if (Array.isArray(value)) {
        const result: unknown[] = new Array(value.length);
        for (let index = 0; index < value.length; index += 1) {
            const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
            if (!descriptor) continue;
            if (!("value" in descriptor)) throw new TypeError("Canvas project contains an accessor");
            result[index] = cloneJsonValue(descriptor.value, depth + 1, budget);
        }
        return result;
    }
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) throw new TypeError("Canvas project contains a non-JSON object");
    const result: Record<string, unknown> = {};
    for (const [key, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(value))) {
        if (!descriptor.enumerable) continue;
        if (!("value" in descriptor)) throw new TypeError("Canvas project contains an accessor");
        Object.defineProperty(result, key, {
            value: cloneJsonValue(descriptor.value, depth + 1, budget),
            enumerable: true,
            configurable: true,
            writable: true,
        });
    }
    return result;
}

function canonicalJson(value: unknown): string {
    const sort = (item: unknown): unknown => {
        if (Array.isArray(item)) return item.map(sort);
        if (!item || typeof item !== "object") return item;
        return Object.fromEntries(Object.entries(item)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([key, child]) => [key, sort(child)]));
    };
    return JSON.stringify(sort(value));
}

function normalizeNode(node: CanvasNodeInput): CanvasNodeData {
    const metadata = node.metadata ? { ...node.metadata } : undefined;
    if (!isBuiltInGraphNode(node.type)) return { ...node, position: { ...node.position }, metadata: metadata as CanvasNodeMetadata | undefined };
    return {
        ...node,
        position: { ...node.position },
        metadata: { ...metadata, graph: normalizeGraphMetadata(node, metadata) },
    };
}

function isBuiltInGraphNode(type: CanvasNodeData["type"]) {
    return type === CanvasNodeType.Text || type === CanvasNodeType.Config || type === CanvasNodeType.Image || type === CanvasNodeType.Video || type === CanvasNodeType.Audio;
}

function normalizeGraphMetadata(node: CanvasNodeInput, metadata?: CanvasNodeInput["metadata"]): CanvasGraphNodeMetadata {
    if (isCurrentGraphMetadata(metadata?.graph)) return cloneGraphMetadata(metadata.graph);
    if (node.type === CanvasNodeType.Text) {
        return {
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "prompt",
            text: metadata?.content ?? metadata?.composerContent ?? metadata?.prompt ?? "",
            outputPortId: "prompt",
        };
    }
    if (node.type === CanvasNodeType.Config) {
        return {
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "model",
            modelId: metadata?.model ?? "",
            operation: inferLegacyOperation(metadata),
            inputPortIds: ["prompt", "reference_images", "first_frame", "last_frame", "reference_video", "reference_audio"],
            outputPortId: "result",
            parameters: scalarParameters(metadata?.params),
        };
    }
    const mediaType = mediaTypeForNode(node.type) ?? "image";
    return {
        schemaVersion: GRAPH_SCHEMA_VERSION,
        role: "result",
        mediaType,
        outputPortId: "media",
        ...(metadata?.sourceJobId ? { jobId: metadata.sourceJobId } : {}),
    };
}

function isCurrentGraphMetadata(value: unknown): value is CanvasGraphNodeMetadata {
    if (!value || typeof value !== "object") return false;
    const candidate = value as Record<string, unknown>;
    if (candidate.schemaVersion !== GRAPH_SCHEMA_VERSION) return false;
    if (candidate.role === "prompt") return typeof candidate.text === "string" && typeof candidate.outputPortId === "string";
    if (candidate.role === "media-collection") {
        return isMediaType(candidate.mediaType)
            && typeof candidate.outputPortId === "string"
            && Array.isArray(candidate.items)
            && candidate.items.every(isGraphMediaItem);
    }
    if (candidate.role === "model") {
        return typeof candidate.modelId === "string"
            && typeof candidate.operation === "string"
            && typeof candidate.outputPortId === "string"
            && Array.isArray(candidate.inputPortIds)
            && candidate.inputPortIds.every((port) => typeof port === "string")
            && isParameterRecord(candidate.parameters);
    }
    if (candidate.role === "result") {
        return isMediaType(candidate.mediaType)
            && typeof candidate.outputPortId === "string"
            && (candidate.assetId === undefined || typeof candidate.assetId === "string")
            && (candidate.jobId === undefined || typeof candidate.jobId === "string");
    }
    return false;
}

function isMediaType(value: unknown): value is GraphMediaType {
    return value === "image" || value === "video" || value === "audio";
}

function isGraphMediaItem(value: unknown) {
    if (!value || typeof value !== "object") return false;
    const item = value as Record<string, unknown>;
    return typeof item.id === "string"
        && typeof item.assetId === "string"
        && typeof item.displayName === "string"
        && typeof item.mimeType === "string"
        && isFiniteNonNegative(item.bytes)
        && (item.width === undefined || isFiniteNonNegative(item.width))
        && (item.height === undefined || isFiniteNonNegative(item.height))
        && (item.durationMs === undefined || isFiniteNonNegative(item.durationMs));
}

function isFiniteNonNegative(value: unknown) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isParameterRecord(value: unknown): value is Record<string, GraphParameterValue> {
    return Boolean(value && typeof value === "object" && !Array.isArray(value) && Object.values(value).every((item) => item === null || typeof item === "string" || typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item))));
}

function cloneGraphMetadata(metadata: CanvasGraphNodeMetadata): CanvasGraphNodeMetadata {
    if (metadata.role === "media-collection") return { ...metadata, items: metadata.items.map((item) => ({ ...item })) };
    if (metadata.role === "model") return { ...metadata, inputPortIds: [...metadata.inputPortIds], parameters: { ...metadata.parameters } };
    return { ...metadata };
}

function inferLegacyOperation(metadata?: CanvasNodeInput["metadata"]) {
    if (metadata?.generationMode === "video") return "video.generate";
    if (metadata?.generationMode === "audio") return "audio.generate";
    if (metadata?.generationType === "edit") return "image.edit";
    return "image.generate";
}

function scalarParameters(value: unknown): Record<string, GraphParameterValue> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, GraphParameterValue] => {
        const item = entry[1];
        return item === null || typeof item === "string" || typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item));
    }));
}

function mediaTypeForNode(type: CanvasNodeData["type"]): GraphMediaType | null {
    if (type === CanvasNodeType.Image) return "image";
    if (type === CanvasNodeType.Video) return "video";
    if (type === CanvasNodeType.Audio) return "audio";
    return null;
}

function normalizeConnections(connections: CanvasConnectionInput[], nodes: CanvasNodeData[]): CanvasConnection[] {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const seen = new Set<string>();
    const promptTargets = new Set<string>();
    const normalized: CanvasConnection[] = [];
    for (const connection of connections) {
        const from = byId.get(connection.fromNodeId);
        const to = byId.get(connection.toNodeId);
        if (!from || !to || from.id === to.id) continue;
        const hasFromPort = Object.prototype.hasOwnProperty.call(connection, "fromPortId");
        const hasToPort = Object.prototype.hasOwnProperty.call(connection, "toPortId");
        if (hasFromPort !== hasToPort) continue;
        const explicit = hasFromPort && hasToPort;
        if (explicit && (!connection.fromPortId || !connection.toPortId)) continue;
        const ports = explicit
            ? { fromPortId: connection.fromPortId!, toPortId: connection.toPortId! }
            : inferLegacyPorts(from, to);
        if (!ports) continue;
        if (explicit && !isValidExplicitConnection(from, ports.fromPortId, to, ports.toPortId)) continue;
        const key = `${from.id}\u0000${ports.fromPortId}\u0000${to.id}\u0000${ports.toPortId}`;
        if (seen.has(key)) continue;
        if (ports.toPortId === "prompt" && promptTargets.has(to.id)) continue;
        seen.add(key);
        if (ports.toPortId === "prompt") promptTargets.add(to.id);
        normalized.push({ id: connection.id, fromNodeId: from.id, fromPortId: ports.fromPortId, toNodeId: to.id, toPortId: ports.toPortId });
    }
    return normalized;
}

function isValidExplicitConnection(from: CanvasNodeData, fromPortId: string, to: CanvasNodeData, toPortId: string) {
    const source = from.metadata?.graph;
    const target = to.metadata?.graph;
    if (!source && !target) return !isBuiltInGraphNode(from.type) && !isBuiltInGraphNode(to.type);
    if (!source && target?.role === "model") {
        return !isBuiltInGraphNode(from.type) && target.inputPortIds.includes(toPortId) && !isReservedModelInput(toPortId);
    }
    if (!source || fromPortId !== source.outputPortId) return false;
    if (!target) return !isBuiltInGraphNode(to.type);
    if (target.role === "model") {
        if (!target.inputPortIds.includes(toPortId)) return false;
        if (toPortId === "prompt") return source.role === "prompt";
        if (toPortId === "reference_images" || toPortId === "first_frame" || toPortId === "last_frame") return isMediaSource(source, "image");
        if (toPortId === "reference_video") return isMediaSource(source, "video");
        if (toPortId === "reference_audio") return isMediaSource(source, "audio");
        return true;
    }
    return target.role === "result" && toPortId === "result" && source.role === "model";
}

function isReservedModelInput(portId: string) {
    return ["prompt", "reference_images", "first_frame", "last_frame", "reference_video", "reference_audio"].includes(portId);
}

function isMediaSource(metadata: CanvasGraphNodeMetadata, mediaType: GraphMediaType) {
    return (metadata.role === "media-collection" || metadata.role === "result") && metadata.mediaType === mediaType;
}

function inferLegacyPorts(from: CanvasNodeData, to: CanvasNodeData) {
    const source = from.metadata?.graph;
    const target = to.metadata?.graph;
    if (source?.role === "prompt" && target?.role === "model") return { fromPortId: "prompt", toPortId: "prompt" };
    if ((source?.role === "media-collection" || source?.role === "result") && target?.role === "model") {
        const toPortId = source.mediaType === "image" ? "reference_images" : source.mediaType === "video" ? "reference_video" : "reference_audio";
        return { fromPortId: "media", toPortId };
    }
    if (source?.role === "model" && target?.role === "result") return { fromPortId: "result", toPortId: "result" };
    return null;
}
