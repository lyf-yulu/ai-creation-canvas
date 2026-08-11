export const GRAPH_SCHEMA_VERSION = 1 as const;

export type GraphParameterValue = string | number | boolean | null;
export type GraphMediaType = "image" | "video" | "audio";
export type GraphNodeRole = "prompt" | "media-collection" | "model" | "result";

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
    inputPortIds: string[];
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
