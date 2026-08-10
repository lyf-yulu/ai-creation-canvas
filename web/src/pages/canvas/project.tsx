import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { nanoid } from "nanoid";

import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { appendResultNode } from "@/features/generation/result-node";
import { useGenerationJob, type PendingRef } from "@/features/generation/use-generation-job";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType, type CanvasNodeData, type ViewportTransform } from "@/types/canvas";
import type { JobState } from "@/api/contracts";
import { fetchModels } from "@/api/models";
import { modelsForOperation, parameterControls } from "@/components/model-picker";
import type { ModelSpec } from "@/api/contracts";

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
    const [models, setModels] = useState<ModelSpec[]>([]);
    const [modelId, setModelId] = useState("");
    const [params, setParams] = useState<Record<string, unknown>>({});
    const project = useCanvasStore((state) => state.openProject(id));
    const updateProject = useCanvasStore((state) => state.updateProject);
    const availableModels = useMemo(() => modelsForOperation(models, operation, "text"), [models, operation]);
    const selectedModel = availableModels.find((model) => model.model_id === modelId) || availableModels[0];
    const controls = parameterControls(selectedModel?.parameter_schema || {});
    const invalidParam = controls.some((control) => { const value = params[control.name]; if (control.required && (value === undefined || value === "")) return true; if ((control.type === "number" || control.type === "integer") && (typeof value !== "number" || !Number.isFinite(value) || (control.type === "integer" && !Number.isInteger(value)) || (control.minimum !== undefined && value < control.minimum) || (control.maximum !== undefined && value > control.maximum))) return true; return control.type === "enum" && value !== undefined && !control.enum?.includes(value as string | number); });
    useEffect(() => { void fetchModels().then(setModels).catch(() => setModels([])); }, []);
    useEffect(() => { setModelId(availableModels[0]?.model_id || ""); }, [operation, models]);
    useEffect(() => { setParams(Object.fromEntries(controls.filter((control) => control.default !== undefined).map((control) => [control.name, control.default]))); }, [selectedModel]);
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
        if (!selectedModel || invalidParam) return;
        const source = generationSource(operation, text, selectedModel.model_id, project.nodes.length);
        updateProject(id, { nodes: [...project.nodes, source] });
        try { await generation.submit({ operation, model_id: selectedModel.model_id, prompt: text, params, asset_ids: [], sourceNodeId: source.id }); } catch { /* The hook has already recorded a safe failure node. */ }
    };
    const label = operation === "video.generate" ? "生成视频" : "生成图片";
    return <main className="h-full"><InfiniteCanvas containerRef={containerRef} viewport={viewport} onViewportChange={setViewport}><section className="m-8 w-[360px] rounded-xl border bg-background p-5 shadow-sm" data-canvas-no-zoom><h1 className="text-lg font-medium">画布 {id}</h1><label className="mt-4 block text-sm" htmlFor="canvas-generation-prompt">提示词</label><textarea id="canvas-generation-prompt" className="mt-1 w-full rounded border p-2" value={prompt} onChange={(event) => setPrompt(event.target.value)} /><select aria-label="生成类型" className="mt-3 w-full rounded border p-2" value={operation} onChange={(event) => setOperation(event.target.value as typeof operation)}><option value="image.generate">图片</option><option value="video.generate">视频</option></select><select aria-label="模型" className="mt-3 w-full rounded border p-2" value={selectedModel?.model_id || ""} onChange={(event) => setModelId(event.target.value)}>{availableModels.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name}</option>)}</select>{controls.map((control) => <label key={control.name} className="mt-2 block text-sm">{control.name}{control.type === "enum" ? <select aria-label={control.name} value={String(params[control.name] ?? control.default ?? "")} onChange={(event) => setParams((current) => ({ ...current, [control.name]: event.target.value }))}>{control.enum?.map((value) => <option key={String(value)} value={String(value)}>{String(value)}</option>)}</select> : <input aria-label={control.name} className="ml-2 rounded border p-1" type={control.type === "boolean" ? "checkbox" : "text"} checked={control.type === "boolean" ? Boolean(params[control.name] ?? control.default) : undefined} value={control.type === "boolean" ? undefined : String(params[control.name] ?? control.default ?? "")} onChange={(event) => setParams((current) => ({ ...current, [control.name]: control.type === "boolean" ? event.target.checked : control.type === "integer" || control.type === "number" ? Number(event.target.value) : event.target.value }))} />}</label>)}{invalidParam ? <p className="text-sm text-red-600">请填写有效参数。</p> : null}<button className="mt-3 rounded bg-stone-900 px-3 py-2 text-sm text-white" onClick={() => void submit()} disabled={!prompt.trim() || !selectedModel || invalidParam || generation.state.status === "submitting"}>{label}</button>{generation.state.message ? <p className="mt-3 text-sm text-red-600">{generation.state.message}</p> : null}<p className="mt-3 text-xs text-stone-500">生成操作仅会通过受控的同源任务 API 提交。</p></section>{project?.nodes.map((node) => <div key={node.id} data-node-id={node.id} className="absolute rounded border bg-background p-2 text-xs" style={{ left: node.position.x, top: node.position.y, width: node.width }}><strong>{node.title}</strong><p>{node.metadata?.status === "error" ? node.metadata.errorDetails : node.metadata?.content || node.metadata?.prompt}</p>{node.metadata?.status === "error" && node.metadata.idempotencyKey ? <button onClick={() => void generation.retry(node.metadata!.idempotencyKey!)}>重试</button> : null}</div>)}</InfiniteCanvas></main>;
}
