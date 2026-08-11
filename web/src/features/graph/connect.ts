import { STANDARD_MODEL_INPUT_PORTS, graphInputPortDescriptor, type GraphPortValueType } from "@/features/graph/contracts";
import { nodeRegistry, type NodeRegistry } from "@/features/nodes/registry";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

export type GraphPortRef = Readonly<{
    nodeId: string;
    portId: string;
    direction: "source" | "target";
    valueType?: GraphPortValueType;
}>;

export type GraphConnectionResult =
    | { ok: true; connection: CanvasConnection }
    | { ok: false; reason: "self" | "duplicate" | "incompatible" | "prompt-occupied" };

export function graphConnectionRejectionMessage(reason: Extract<GraphConnectionResult, { ok: false }>["reason"]) {
    if (reason === "self") return "不能连接同一个节点。";
    if (reason === "duplicate") return "这两个端口已经连接。";
    if (reason === "prompt-occupied") return "该模型已有提示词连接，每个模型只允许一个提示词节点。";
    return "这两个端口类型不兼容。";
}

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

export function getNodePorts(node: CanvasNodeData, registry: Pick<NodeRegistry, "getNode"> = nodeRegistry): { sources: GraphPortRef[]; targets: GraphPortRef[] } {
    const graph = node.metadata?.graph;
    if (graph?.role === "prompt") {
        return { sources: [sourcePort(node.id, graph.outputPortId, "prompt")], targets: [] };
    }
    if (graph?.role === "media-collection" || graph?.role === "result") {
        return { sources: [sourcePort(node.id, graph.outputPortId, graph.mediaType)], targets: [] };
    }
    if (graph?.role === "model") {
        const inputPorts = Array.isArray(graph.inputPorts)
            ? graph.inputPorts
            : ((graph as unknown as { inputPortIds?: unknown }).inputPortIds as unknown[] | undefined)?.filter((portId): portId is string => typeof portId === "string").map(graphInputPortDescriptor) ?? [];
        return {
            sources: [sourcePort(node.id, graph.outputPortId, "any")],
            targets: inputPorts.map((descriptor) => targetPort(node.id, descriptor.id, descriptor.accepts)),
        };
    }
    const definition = registry.getNode(String(node.type));
    if (!definition) return { sources: [], targets: [] };
    return {
        sources: definition.outputs.map((declaration) => typeof declaration === "string"
            ? sourcePort(node.id, declaration, "any")
            : sourcePort(node.id, declaration.id, declaration.provides)),
        targets: definition.inputs.map((declaration) => typeof declaration === "string"
            ? targetPort(node.id, declaration, "any")
            : targetPort(node.id, declaration.id, declaration.accepts)),
    };
}

export function connectGraphPorts(
    first: GraphPortRef,
    second: GraphPortRef,
    nodes: readonly CanvasNodeData[],
    connections: readonly CanvasConnection[],
    connectionId: string,
    registry: Pick<NodeRegistry, "getNode"> = nodeRegistry,
): GraphConnectionResult {
    const sourceCandidate = first.direction === "source" ? first : second.direction === "source" ? second : null;
    const targetCandidate = first.direction === "target" ? first : second.direction === "target" ? second : null;
    if (!sourceCandidate || !targetCandidate) return { ok: false, reason: "incompatible" };
    if (sourceCandidate.nodeId === targetCandidate.nodeId) return { ok: false, reason: "self" };

    const sourceNode = nodes.find((node) => node.id === sourceCandidate.nodeId);
    const targetNode = nodes.find((node) => node.id === targetCandidate.nodeId);
    if (!sourceNode || !targetNode) return { ok: false, reason: "incompatible" };
    const source = getNodePorts(sourceNode, registry).sources.find((candidate) => candidate.portId === sourceCandidate.portId);
    const target = getNodePorts(targetNode, registry).targets.find((candidate) => candidate.portId === targetCandidate.portId);
    if (!source || !target || !portsAreCompatible(source, target, targetNode)) return { ok: false, reason: "incompatible" };

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

function sourcePort(nodeId: string, portId: string, valueType: GraphPortValueType): GraphPortRef {
    return { nodeId, portId, direction: "source", valueType };
}

function targetPort(nodeId: string, portId: string, valueType: GraphPortValueType): GraphPortRef {
    return { nodeId, portId, direction: "target", valueType };
}

function portsAreCompatible(source: GraphPortRef, target: GraphPortRef, targetNode: CanvasNodeData) {
    const targetGraph = targetNode.metadata?.graph;
    const standard = targetGraph?.role === "model" || targetNode.type === "config" ? STANDARD_MODEL_INPUT_PORTS[target.portId] : undefined;
    if (standard) return source.valueType === standard.accepts;
    return source.valueType === "any" || target.valueType === "any" || source.valueType === target.valueType;
}
