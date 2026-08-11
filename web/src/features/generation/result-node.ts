import { nanoid } from "nanoid";
import { safeApiPath } from "@/api/client";
import type { JobState } from "@/api/contracts";
import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";

export function createResultNode(job: JobState, source?: CanvasNodeData, resultIndex = 0): CanvasNodeData {
    const declared = job.results?.[resultIndex];
    const url = declared?.url ?? (resultIndex === 0 ? job.result_url : undefined);
    if (!url) throw new Error("A successful job requires a result URL");
    const isVideo = declared?.media_type === "video" || (!declared && (job.operation?.startsWith("video.") ?? false));
    return {
        id: nanoid(),
        type: isVideo ? CanvasNodeType.Video : CanvasNodeType.Image,
        title: isVideo ? "生成视频" : "生成图片",
        position: source ? { x: source.position.x + 48 + resultIndex * 28, y: source.position.y + 48 + resultIndex * 28 } : { x: 80 + resultIndex * 28, y: 80 + resultIndex * 28 },
        width: isVideo ? 420 : 340,
        height: isVideo ? 236 : 240,
        metadata: {
            content: safeApiPath(url),
            status: "success",
            sourceJobId: job.id,
            sourceResultIndex: resultIndex,
            graph: {
                schemaVersion: GRAPH_SCHEMA_VERSION,
                role: "result",
                mediaType: isVideo ? "video" : "image",
                inputPortId: "result",
                outputPortId: "media",
                jobId: job.id,
                assetId: declared?.asset_id,
            },
        },
    };
}

/** Idempotent across refresh, concurrent resume and React StrictMode. */
export function appendResultNode(nodes: readonly CanvasNodeData[], job: JobState, source?: CanvasNodeData) {
    const count = job.results?.length || (job.result_url ? 1 : 0);
    const additions = Array.from({ length: count }, (_, index) => index)
        .filter((index) => !nodes.some((node) => node.metadata?.sourceJobId === job.id && (node.metadata.sourceResultIndex ?? 0) === index))
        .map((index) => createResultNode(job, source, index));
    return [...nodes, ...additions];
}
