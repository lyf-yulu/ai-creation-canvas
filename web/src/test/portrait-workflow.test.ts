import { expect, it, vi } from "vitest";
import { portraitVideoWorkflow } from "@/features/workflows/portrait-video";

const file = new File(["image"], "portrait.png", { type: "image/png" });

it("waits through processing before submitting a portrait video", async () => {
  const calls: string[] = [];
  const output = await portraitVideoWorkflow.run({
    file, modelId: "portrait-video", prompt: "wave", params: {}, idempotencyKey: "key",
    uploadAsset: async () => { calls.push("upload:portrait"); return { id: "asset-1", kind: "portrait", status: "processing", mime_type: "image/png" }; },
    fetchAsset: async () => { calls.push("poll:asset-1"); return { id: "asset-1", kind: "portrait", status: "active", mime_type: "image/png" }; },
    submitJob: async (request) => { calls.push(`submit:${request.operation}:${request.asset_ids[0]}`); return { jobId: "job-1" }; },
    sleep: async () => {}, pollIntervalMs: 1, maxWaitMs: 2,
  });
  expect(output).toEqual({ jobId: "job-1", assetId: "asset-1" });
  expect(calls).toEqual(["upload:portrait", "poll:asset-1", "submit:video.image_to_video:asset-1"]);
});

it("reuses an existing active local asset without uploading", async () => {
  const uploadAsset = vi.fn(); const fetchAsset = vi.fn(async () => ({ id: "asset-1", kind: "portrait" as const, status: "active" as const, mime_type: "image/png" }));
  await portraitVideoWorkflow.run({ assetId: "asset-1", modelId: "portrait-video", prompt: "wave", params: {}, idempotencyKey: "key", uploadAsset, fetchAsset, submitJob: async () => ({ jobId: "job-1" }) });
  expect(uploadAsset).not.toHaveBeenCalled();
});
