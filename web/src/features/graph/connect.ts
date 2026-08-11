import type { GraphMediaType } from "@/features/graph/contracts";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

export type GraphPortRef = Readonly<{
    nodeId: string;
    portId: string;
    direction: "source" | "target";
    mediaType?: GraphMediaType;
}>;

export type GraphConnectionResult =
    | { ok: true; connection: CanvasConnection }
    | { ok: false; reason: "self" | "duplicate" | "incompatible" | "prompt-occupied" };

export const GRAPH_PORT_IDS = {
    prompt: "prompt",
    referenceImages: "reference_images",
    firstFrame: "first_frame",
    lastFrame: "last_frame",
    referenceVideo: "reference_video",
    referenceAudio: "reference_audio",
    result: "result",
    media: "media",
} as const;

const MODEL_INPUT_MEDIA: Readonly<Record<string, GraphMediaType | undefined>> = {
    [GRAPH_PORT_IDS.prompt]: undefined,
    [GRAPH_PORT_IDS.referenceImages]: "image",
    [GRAPH_PORT_IDS.firstFrame]: "image",
    [GRAPH_PORT_IDS.lastFrame]: "image",
    [GRAPH_PORT_IDS.referenceVideo]: "video",
    [GRAPH_PORT_IDS.referenceAudio]: "audio",
};

export function getNodePorts(node: CanvasNodeData): { sources: GraphPortRef[]; targets: GraphPortRef[] } {
    const graph = node.metadata?.graph;
    if (graph?.role === "prompt") {
        return { sources: [sourcePort(node.id, graph.outputPortId)], targets: [] };
    }
    if (graph?.role === "media-collection" || graph?.role === "result") {
        return { sources: [sourcePort(node.id, graph.outputPortId, graph.mediaType)], targets: [] };
    }
    if (graph?.role === "model") {
        return {
            sources: [sourcePort(node.id, graph.outputPortId)],
            targets: graph.inputPortIds.map((portId) => ({ nodeId: node.id, portId, direction: "target" as const, mediaType: MODEL_INPUT_MEDIA[portId] })),
        };
    }
    if (node.type === "text") return { sources: [sourcePort(node.id, GRAPH_PORT_IDS.prompt)], targets: [] };
    if (node.type === "image") return { sources: [sourcePort(node.id, GRAPH_PORT_IDS.media, "image")], targets: [] };
    if (node.type === "video") return { sources: [sourcePort(node.id, GRAPH_PORT_IDS.media, "video")], targets: [] };
    if (node.type === "audio") return { sources: [sourcePort(node.id, GRAPH_PORT_IDS.media, "audio")], targets: [] };
    return { sources: [], targets: [] };
}

export function connectGraphPorts(
    first: GraphPortRef,
    second: GraphPortRef,
    nodes: readonly CanvasNodeData[],
    connections: readonly CanvasConnection[],
    connectionId: string,
): GraphConnectionResult {
    const sourceCandidate = first.direction === "source" ? first : second.direction === "source" ? second : null;
    const targetCandidate = first.direction === "target" ? first : second.direction === "target" ? second : null;
    if (!sourceCandidate || !targetCandidate) return { ok: false, reason: "incompatible" };
    if (sourceCandidate.nodeId === targetCandidate.nodeId) return { ok: false, reason: "self" };

    const sourceNode = nodes.find((node) => node.id === sourceCandidate.nodeId);
    const targetNode = nodes.find((node) => node.id === targetCandidate.nodeId);
    if (!sourceNode || !targetNode) return { ok: false, reason: "incompatible" };
    const source = getNodePorts(sourceNode).sources.find((candidate) => candidate.portId === sourceCandidate.portId);
    const target = getNodePorts(targetNode).targets.find((candidate) => candidate.portId === targetCandidate.portId);
    if (!source || !target || !portsAreCompatible(source, target, sourceNode, targetNode)) return { ok: false, reason: "incompatible" };

    const duplicate = connections.some((connection) => connection.fromNodeId === source.nodeId
        && connection.fromPortId === source.portId
        && connection.toNodeId === target.nodeId
        && connection.toPortId === target.portId);
    if (duplicate) return { ok: false, reason: "duplicate" };
    if (target.portId === GRAPH_PORT_IDS.prompt && connections.some((connection) => connection.toNodeId === target.nodeId && connection.toPortId === GRAPH_PORT_IDS.prompt)) {
        return { ok: false, reason: "prompt-occupied" };
    }
    return {
        ok: true,
        connection: {
            id: connectionId,
            fromNodeId: source.nodeId,
            fromPortId: source.portId,
            toNodeId: target.nodeId,
            toPortId: target.portId,
        },
    };
}

function sourcePort(nodeId: string, portId: string, mediaType?: GraphMediaType): GraphPortRef {
    return { nodeId, portId, direction: "source", mediaType };
}

function portsAreCompatible(source: GraphPortRef, target: GraphPortRef, sourceNode: CanvasNodeData, targetNode: CanvasNodeData) {
    const targetGraph = targetNode.metadata?.graph;
    if (targetGraph?.role !== "model") return false;
    if (target.portId === GRAPH_PORT_IDS.prompt) return sourceNode.metadata?.graph?.role === "prompt" || sourceNode.type === "text";
    const requiredMedia = MODEL_INPUT_MEDIA[target.portId];
    return requiredMedia !== undefined && source.mediaType === requiredMedia;
}
