export const GRAPH_SCHEMA_VERSION = 1 as const;

export type GraphParameterValue = string | number | boolean | null;
export type GraphMediaType = "image" | "video" | "audio";
export type GraphNodeRole = "prompt" | "media-collection" | "model" | "result";
export type GraphPortValueType = "prompt" | GraphMediaType | "any";

export type GraphInputPortDescriptor = {
    id: string;
    accepts: GraphPortValueType;
};

export type GraphOutputPortDescriptor = {
    id: string;
    provides: GraphPortValueType;
};

const standardModelInputPorts = {
    prompt: { id: "prompt", accepts: "prompt" },
    reference_images: { id: "reference_images", accepts: "image" },
    first_frame: { id: "first_frame", accepts: "image" },
    last_frame: { id: "last_frame", accepts: "image" },
    reference_video: { id: "reference_video", accepts: "video" },
    reference_audio: { id: "reference_audio", accepts: "audio" },
} as const satisfies Record<string, GraphInputPortDescriptor>;

export const STANDARD_MODEL_INPUT_PORTS: Readonly<Record<string, Readonly<GraphInputPortDescriptor>>> = Object.freeze(
    Object.fromEntries(Object.entries(standardModelInputPorts).map(([id, descriptor]) => [id, Object.freeze({ ...descriptor })])),
);

export function graphInputPortDescriptor(portId: string): GraphInputPortDescriptor {
    const standard = STANDARD_MODEL_INPUT_PORTS[portId];
    return standard ? { ...standard } : { id: portId, accepts: "any" };
}

export type GraphMediaItem = Readonly<{
    id: string;
    assetId: string;
    displayName: string;
    mimeType: string;
    bytes: number;
    width?: number;
    height?: number;
    durationMs?: number;
}>;

export type GraphPromptMetadata = {
    schemaVersion: typeof GRAPH_SCHEMA_VERSION;
    role: "prompt";
    text: string;
    outputPortId: string;
};

export type GraphMediaCollectionMetadata = {
    schemaVersion: typeof GRAPH_SCHEMA_VERSION;
    role: "media-collection";
    mediaType: GraphMediaType;
    outputPortId: string;
    items: GraphMediaItem[];
};

export type GraphModelMetadata = {
    schemaVersion: typeof GRAPH_SCHEMA_VERSION;
    role: "model";
    modelId: string;
    operation: string;
    inputPorts: GraphInputPortDescriptor[];
    outputPortId: string;
    parameters: Record<string, GraphParameterValue>;
};

export type GraphResultMetadata = {
    schemaVersion: typeof GRAPH_SCHEMA_VERSION;
    role: "result";
    mediaType: GraphMediaType;
    outputPortId: string;
    assetId?: string;
    jobId?: string;
};

export type CanvasGraphNodeMetadata = GraphPromptMetadata | GraphMediaCollectionMetadata | GraphModelMetadata | GraphResultMetadata;

export type GraphSubmissionInput = Readonly<{
    portId: string;
    mediaType: GraphMediaType;
    assetIds: readonly string[];
}>;

export type GraphSubmissionSnapshot = Readonly<{
    schemaVersion: typeof GRAPH_SCHEMA_VERSION;
    prompt: string;
    modelId: string;
    operation: string;
    parameters: Readonly<Record<string, GraphParameterValue>>;
    inputs: readonly GraphSubmissionInput[];
}>;

export type GraphSubmissionSnapshotSource = Omit<GraphSubmissionSnapshot, "schemaVersion">;

export function createGraphSubmissionSnapshot(source: GraphSubmissionSnapshotSource): GraphSubmissionSnapshot {
    const parameters = Object.freeze({ ...source.parameters });
    const inputs = Object.freeze(source.inputs.map((input) => Object.freeze({
        portId: input.portId,
        mediaType: input.mediaType,
        assetIds: Object.freeze([...input.assetIds]),
    })));
    return Object.freeze({
        schemaVersion: GRAPH_SCHEMA_VERSION,
        prompt: source.prompt,
        modelId: source.modelId,
        operation: source.operation,
        parameters,
        inputs,
    });
}
