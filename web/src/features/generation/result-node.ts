import { nanoid } from "nanoid";
import { safeApiPath } from "@/api/client";
import type { JobState } from "@/api/contracts";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

export function createResultNode(job: JobState, source?: CanvasNodeData): CanvasNodeData {
    if (!job.result_url) throw new Error("A successful job requires a result URL");
    const isVideo = job.operation?.startsWith("video.") ?? false;
    return {
        id: nanoid(), type: isVideo ? CanvasNodeType.Video : CanvasNodeType.Image,
        title: isVideo ? "生成视频" : "生成图片",
        position: source ? { x: source.position.x + 48, y: source.position.y + 48 } : { x: 80, y: 80 },
        width: isVideo ? 420 : 340, height: isVideo ? 236 : 240,
        metadata: { content: safeApiPath(job.result_url), status: "success", sourceJobId: job.id },
    };
}

/** Idempotent across refresh, concurrent resume and React StrictMode. */
export function appendResultNode(nodes: readonly CanvasNodeData[], job: JobState, source?: CanvasNodeData) {
    return nodes.some((node) => node.metadata?.sourceJobId === job.id) ? [...nodes] : [...nodes, createResultNode(job, source)];
}
