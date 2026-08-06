import type { AssetRef } from "@/api/contracts";
import { registerWorkflow } from "./registry";
import type { PortraitVideoInput, PortraitVideoOutput, WorkflowDefinition } from "./types";

const defaultSleep = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

async function waitForActiveAsset(asset: AssetRef, input: PortraitVideoInput) {
    const interval = input.pollIntervalMs ?? 1_000;
    const maximum = input.maxWaitMs ?? 120_000;
    const sleep = input.sleep ?? defaultSleep;
    for (let elapsed = 0; elapsed <= maximum; elapsed += interval) {
        const current = elapsed === 0 ? asset : await input.fetchAsset(asset.id);
        if (current.status === "active") return current;
        if (current.status === "failed") throw new Error(`asset ${current.id} failed`);
        if (elapsed + interval > maximum) break;
        await sleep(interval);
    }
    throw new Error(`asset ${asset.id} timed out`);
}

export const portraitVideoWorkflow: WorkflowDefinition<PortraitVideoInput, PortraitVideoOutput> = {
    id: "portrait.video",
    version: 1,
    async run(input) {
        const uploaded = await input.uploadAsset(input.file, "portrait");
        const asset = await waitForActiveAsset(uploaded, input);
        const job = await input.submitJob({ operation: "video.image_to_video", model_id: input.modelId, ...(input.serviceId ? { service_id: input.serviceId } : {}), prompt: input.prompt, params: input.params, asset_ids: [asset.id], idempotency_key: input.idempotencyKey });
        return { jobId: job.jobId, assetId: asset.id };
    },
};

let registered = false;
export function registerBuiltinWorkflows() {
    if (registered) return;
    registerWorkflow(portraitVideoWorkflow);
    registered = true;
}
