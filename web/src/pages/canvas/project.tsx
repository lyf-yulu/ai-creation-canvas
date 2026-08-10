import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { ImagePlus, MessageSquareText } from "lucide-react";
import { nanoid } from "nanoid";

import type { JobState, ModelSpec } from "@/api/contracts";
import { fetchModels } from "@/api/models";
import { DraggableCanvasNode } from "@/components/canvas/draggable-canvas-node";
import { GenerationInspector, type GenerationInspectorValue } from "@/components/canvas/generation-inspector";
import { GenerationNodeCard } from "@/components/canvas/generation-node-card";
import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { CanvasNavigationControls } from "@/components/canvas/canvas-navigation-controls";
import { normalizeViewport } from "@/features/canvas/viewport";
import { appendResultNode } from "@/features/generation/result-node";
import { useGenerationJob, type PendingRef } from "@/features/generation/use-generation-job";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType } from "@/types/canvas";
import type { CanvasNodeData, Position, ViewportTransform } from "@/types/canvas";


function generationSource(prompt: string, model: string, position: number): CanvasNodeData {
    return { id: nanoid(), type: CanvasNodeType.Config, title: "图片生成", position: { x: 80 + position * 24, y: 160 + position * 24 }, width: 300, height: 140, metadata: { prompt, model, status: "loading", generationMode: "image" } };
}

export default function CanvasProjectPage() {
    const { id = "" } = useParams();
    const containerRef = useRef<HTMLDivElement>(null);
    const [models, setModels] = useState<ModelSpec[]>([]);
    const [inspector, setInspector] = useState<GenerationInspectorValue>({ prompt: "", modelId: "", params: {} });
    const project = useCanvasStore((state) => state.openProject(id));
    const projectsLoaded = useCanvasStore((state) => state.projectsLoaded);
    const updateProject = useCanvasStore((state) => state.updateProject);
    const viewport = normalizeViewport(project?.viewport);
    const changeViewport = useCallback((next: ViewportTransform) => {
        updateProject(id, { viewport: normalizeViewport(next) });
    }, [id, updateProject]);
    const moveNode = useCallback((nodeId: string, position: Position) => {
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const nodes = current.nodes.map((node) => node.id === nodeId ? { ...node, position } : node);
        updateProject(id, { nodes });
    }, [id, updateProject]);

    useEffect(() => { void fetchModels().then(setModels).catch(() => setModels([])); }, []);

    const onSucceeded = useCallback((job: JobState, ref?: PendingRef) => {
        const targetProjectId = ref?.projectId;
        if (!targetProjectId) return;
        const current = useCanvasStore.getState().openProject(targetProjectId);
        if (!current) return;
        const source = current.nodes.find((node) => node.id === ref?.sourceNodeId);
        const nodes = current.nodes.map((node) => node.id === source?.id ? { ...node, metadata: { ...node.metadata, status: "success" as const } } : node);
        updateProject(targetProjectId, { nodes: appendResultNode(nodes, job, source) });
    }, [updateProject]);

    const onFailed = useCallback(({ request, projectId, sourceNodeId, message, requestId, phase, retryToken }: { request: { operation: "image.generate" | "image.edit" | "video.generate" | "video.image_to_video"; model_id: string; prompt: string; params: Record<string, unknown>; asset_ids: string[]; idempotency_key: string }; projectId?: string; sourceNodeId?: string; message: string; requestId?: string; phase?: string; retryToken?: string }) => {
        if (!projectId) return;
        const current = useCanvasStore.getState().openProject(projectId);
        if (!current) return;
        const source = current.nodes.find((node) => node.id === sourceNodeId);
        const existing = current.nodes.map((node) => node.id === source?.id ? { ...node, metadata: { ...node.metadata, status: "error" as const } } : node);
        const failed: CanvasNodeData = { id: nanoid(), type: CanvasNodeType.Image, title: "生成失败", position: source ? { x: source.position.x + 48, y: source.position.y + 48 } : { x: 80, y: 80 }, width: 340, height: 190, metadata: { status: "error", errorDetails: message, prompt: request.prompt, model: request.model_id, params: request.params, assetIds: request.asset_ids, requestId, phase, idempotencyKey: retryToken } };
        updateProject(projectId, { nodes: [...existing, failed] });
    }, [updateProject]);

    const generation = useGenerationJob({ onSucceeded, onFailed });
    const submit = (model: ModelSpec, safeParams: Record<string, unknown>) => {
        const current = useCanvasStore.getState().openProject(id);
        const prompt = inspector.prompt.trim();
        if (!current || !prompt) return;
        const source = generationSource(prompt, model.model_id, current.nodes.length);
        updateProject(id, { nodes: [...current.nodes, source] });
        void generation.submit({ operation: "image.generate", model_id: model.model_id, prompt, params: safeParams, asset_ids: [], projectId: id, sourceNodeId: source.id }).catch(() => undefined);
    };
    const addPromptNode = () => {
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const node: CanvasNodeData = { id: nanoid(), type: CanvasNodeType.Text, title: "提示词", position: { x: 80 + current.nodes.length * 24, y: 80 + current.nodes.length * 24 }, width: 280, height: 120, metadata: { content: inspector.prompt || "请在右侧输入提示词", status: "idle" } };
        updateProject(id, { nodes: [...current.nodes, node] });
    };

    if (!project) {
        if (!projectsLoaded) return <main className="flex h-full items-center justify-center bg-[#050806] text-[#829889]">正在加载画布…</main>;
        return <Navigate to="/canvas" replace />;
    }

    return <main className="flex h-full min-h-0 flex-col overflow-hidden bg-[#050806] text-[#dceee1] lg:grid lg:grid-cols-[152px_minmax(0,1fr)_340px]">
        <aside data-testid="studio-palette" className="shrink-0 border-b border-[#1d3d28] bg-[#08100b] p-2 lg:border-b-0 lg:border-r lg:p-3"><div className="flex items-center justify-between gap-2 lg:block"><div><p className="px-2 text-xs tracking-[0.16em] text-[#58ed87] lg:pt-2">NODE PALETTE</p><h1 className="px-2 py-1 text-sm font-semibold lg:pb-4 lg:pt-2">{project.title}</h1></div><div className="flex flex-wrap gap-2 lg:block lg:space-y-2"><button type="button" onClick={addPromptNode} className="flex items-center gap-2 rounded-lg border border-[#254b33] bg-[#0d1b12] px-3 py-2 text-left text-xs hover:border-[#4fbd70] lg:w-full lg:py-2.5"><MessageSquareText className="size-4 text-[#58ed87]" />提示词节点</button><button type="button" onClick={() => document.getElementById("studio-prompt")?.focus()} className="flex items-center gap-2 rounded-lg border border-[#254b33] bg-[#0d1b12] px-3 py-2 text-left text-xs hover:border-[#4fbd70] lg:w-full lg:py-2.5"><ImagePlus className="size-4 text-[#58ed87]" />图片生成节点</button></div></div><p className="mt-5 hidden px-2 text-[11px] leading-5 text-[#688371] lg:block">更多能力将按后续切片增量开放。</p></aside>
        <section data-testid="studio-canvas" className="embed-surface relative min-h-0 min-w-0 flex-1"><InfiniteCanvas containerRef={containerRef} viewport={viewport} backgroundMode={project.backgroundMode} onViewportChange={changeViewport}>{project.nodes.map((node) => <DraggableCanvasNode key={node.id} node={node} scale={viewport.k} onPositionChange={moveNode}><GenerationNodeCard node={node} onRetry={(token) => void generation.retry(token).catch(() => undefined)} /></DraggableCanvasNode>)}</InfiniteCanvas><CanvasNavigationControls viewport={viewport} onViewportChange={changeViewport} /></section>
        <GenerationInspector models={models} operation="image.generate" value={inspector} disabled={generation.state.status === "submitting"} message={generation.state.message} onChange={setInspector} onSubmit={submit} />
    </main>;
}
