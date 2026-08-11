import { GRAPH_SCHEMA_VERSION, type CanvasGraphNodeMetadata, type GraphMediaType, type GraphParameterValue } from "@/features/graph/contracts";
import type { CanvasProject } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData, type CanvasNodeMetadata } from "@/types/canvas";

export function normalizeCanvasProject(project: CanvasProject): CanvasProject {
    const nodes = project.nodes.map(normalizeNode);
    return {
        ...project,
        graphSchemaVersion: GRAPH_SCHEMA_VERSION,
        nodes,
        connections: normalizeConnections(project.connections, nodes),
        chatSessions: [...project.chatSessions],
        viewport: { ...project.viewport },
    };
}

function normalizeNode(node: CanvasNodeData): CanvasNodeData {
    const metadata = node.metadata ? { ...node.metadata } : undefined;
    if (!isBuiltInGraphNode(node.type)) return { ...node, position: { ...node.position }, metadata };
    return {
        ...node,
        position: { ...node.position },
        metadata: { ...metadata, graph: normalizeGraphMetadata(node, metadata) },
    };
}

function isBuiltInGraphNode(type: CanvasNodeData["type"]) {
    return type === CanvasNodeType.Text || type === CanvasNodeType.Config || type === CanvasNodeType.Image || type === CanvasNodeType.Video || type === CanvasNodeType.Audio;
}

function normalizeGraphMetadata(node: CanvasNodeData, metadata?: CanvasNodeMetadata): CanvasGraphNodeMetadata {
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
        ...(metadata?.assetIds?.[0] ? { assetId: metadata.assetIds[0] } : {}),
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

function inferLegacyOperation(metadata?: CanvasNodeMetadata) {
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

function normalizeConnections(connections: CanvasConnection[], nodes: CanvasNodeData[]): CanvasConnection[] {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const seen = new Set<string>();
    const promptTargets = new Set<string>();
    const normalized: CanvasConnection[] = [];
    for (const connection of connections) {
        const from = byId.get(connection.fromNodeId);
        const to = byId.get(connection.toNodeId);
        if (!from || !to || from.id === to.id) continue;
        const ports = connection.fromPortId && connection.toPortId
            ? { fromPortId: connection.fromPortId, toPortId: connection.toPortId }
            : inferLegacyPorts(from, to);
        if (!ports) continue;
        const key = `${from.id}\u0000${ports.fromPortId}\u0000${to.id}\u0000${ports.toPortId}`;
        if (seen.has(key)) continue;
        if (ports.toPortId === "prompt" && promptTargets.has(to.id)) continue;
        seen.add(key);
        if (ports.toPortId === "prompt") promptTargets.add(to.id);
        normalized.push({ id: connection.id, fromNodeId: from.id, fromPortId: ports.fromPortId, toNodeId: to.id, toPortId: ports.toPortId });
    }
    return normalized;
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
