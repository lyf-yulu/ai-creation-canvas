import type { AssetRef, JobRequest } from "@/api/contracts";

export type WorkflowDefinition<Input = unknown, Output = unknown> = {
    id: string;
    version: number;
    run: (input: Input) => Promise<Output>;
};

export type PortraitVideoJobRequest = JobRequest;
export class PortraitWorkflowError extends Error {
    readonly assetId: string;
    readonly phase = "video-submit" as const;
    constructor(assetId: string, cause: unknown) {
        super("The portrait video could not be submitted.");
        this.name = "PortraitWorkflowError";
        this.assetId = assetId;
        this.cause = cause;
    }
}
export type PortraitVideoInput = {
    file?: File;
    assetId?: string;
    modelId: string;
    prompt: string;
    params: Record<string, unknown>;
    idempotencyKey: string;
    uploadAsset: (file: File, kind: "portrait") => Promise<AssetRef>;
    fetchAsset: (id: string) => Promise<AssetRef>;
    submitJob: (request: PortraitVideoJobRequest) => Promise<{ jobId: string }>;
    sleep?: (milliseconds: number) => Promise<void>;
    pollIntervalMs?: number;
    maxWaitMs?: number;
};
export type PortraitVideoOutput = { jobId: string; assetId: string };
