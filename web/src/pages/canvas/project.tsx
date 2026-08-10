import { useCallback, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { nanoid } from "nanoid";

import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { appendResultNode } from "@/features/generation/result-node";
import { useGenerationJob, type PendingRef } from "@/features/generation/use-generation-job";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasNodeData, type ViewportTransform } from "@/types/canvas";
import type { JobState } from "@/api/contracts";

function generationSource(operation: "image.generate" | "video.generate", prompt: string, model: string, position: number): CanvasNodeData {
    return { id: nanoid(), type: CanvasNodeType.Config, title: operation === "video.generate" ? "视频生成" : "图片生成", position: { x: 40 + position * 24, y: 180 + position * 24 }, width: 300, height: 140, metadata: { prompt, model, status: "loading", generationMode: operation.startsWith("video") ? "video" : "image" } };
}

/** Canvas generation has no provider client: this page only submits same-origin portal jobs. */
export default function CanvasProjectPage() {
    const { id = "" } = useParams();
    const containerRef = useRef<HTMLDivElement>(null);
    const [viewport, setViewport] = useState<ViewportTransform>({ x: 0, y: 0, k: 1 });
    const [prompt, setPrompt] = useState("");
    const [operation, setOperation] = useState<"image.generate" | "video.generate">("image.generate");
    const project = useCanvasStore((state) => state.openProject(id));
    const updateProject = useCanvasStore((state) => state.updateProject);
    const onSucceeded = useCallback((job: JobState, ref?: PendingRef) => {
        if (!id) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const source = current.nodes.find((node) => node.id === ref?.sourceNodeId);
        updateProject(id, { nodes: appendResultNode(current.nodes, job, source) });
    }, [id, updateProject]);
    const onFailed = useCallback(({ request, sourceNodeId, message, requestId, phase }: { request: { operation: "image.generate" | "image.edit" | "video.generate" | "video.image_to_video"; model_id: string; prompt: string; params: Record<string, unknown>; asset_ids: string[]; idempotency_key: string }; sourceNodeId?: string; message: string; requestId?: string; phase?: string }) => {
        if (!id) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const source = current.nodes.find((node) => node.id === sourceNodeId);
        const video = request.operation.startsWith("video.");
        const failed: CanvasNodeData = { id: nanoid(), type: video ? CanvasNodeType.Video : CanvasNodeType.Image, title: "生成失败", position: source ? { x: source.position.x + 48, y: source.position.y + 48 } : { x: 80, y: 80 }, width: video ? 420 : 340, height: video ? 236 : 240, metadata: { status: "error", errorDetails: message, prompt: request.prompt, model: request.model_id, params: request.params, assetIds: request.asset_ids, requestId, phase, idempotencyKey: request.idempotency_key } };
        updateProject(id, { nodes: [...current.nodes, failed] });
    }, [id, updateProject]);
    const generation = useGenerationJob({ onSucceeded, onFailed });
    const submit = async () => {
        const text = prompt.trim();
        if (!text || !project) return;
        const source = generationSource(operation, text, operation === "video.generate" ? "video-default" : "image-default", project.nodes.length);
        updateProject(id, { nodes: [...project.nodes, source] });
        try { await generation.submit({ operation, model_id: source.metadata?.model || "", prompt: text, params: {}, asset_ids: [], sourceNodeId: source.id }); } catch { /* The hook has already recorded a safe failure node. */ }
    };
    const label = operation === "video.generate" ? "生成视频" : "生成图片";
    return <main className="h-full"><InfiniteCanvas containerRef={containerRef} viewport={viewport} onViewportChange={setViewport}><section className="m-8 w-[360px] rounded-xl border bg-background p-5 shadow-sm" data-canvas-no-zoom><h1 className="text-lg font-medium">画布 {id}</h1><label className="mt-4 block text-sm" htmlFor="canvas-generation-prompt">提示词</label><textarea id="canvas-generation-prompt" className="mt-1 w-full rounded border p-2" value={prompt} onChange={(event) => setPrompt(event.target.value)} /><select aria-label="生成类型" className="mt-3 w-full rounded border p-2" value={operation} onChange={(event) => setOperation(event.target.value as typeof operation)}><option value="image.generate">图片</option><option value="video.generate">视频</option></select><button className="mt-3 rounded bg-stone-900 px-3 py-2 text-sm text-white" onClick={() => void submit()} disabled={!prompt.trim() || generation.state.status === "submitting"}>{label}</button>{generation.state.message ? <p className="mt-3 text-sm text-red-600">{generation.state.message}</p> : null}<p className="mt-3 text-xs text-stone-500">生成操作仅会通过受控的同源任务 API 提交。</p></section>{project?.nodes.map((node) => <div key={node.id} data-node-id={node.id} className="absolute rounded border bg-background p-2 text-xs" style={{ left: node.position.x, top: node.position.y, width: node.width }}><strong>{node.title}</strong><p>{node.metadata?.status === "error" ? node.metadata.errorDetails : node.metadata?.content || node.metadata?.prompt}</p></div>)}</InfiniteCanvas></main>;
}
