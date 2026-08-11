import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { Navigate, useParams } from "react-router-dom";
import { ImagePlus, MessageSquareText } from "lucide-react";
import { nanoid } from "nanoid";

import type { JobState, ModelOperation, ModelSpec } from "@/api/contracts";
import { fetchModels } from "@/api/models";
import { DraggableCanvasNode } from "@/components/canvas/draggable-canvas-node";
import { CanvasNodeContextMenu } from "@/components/canvas/canvas-context-menu";
import { GenerationInspector, type GenerationInspectorValue } from "@/components/canvas/generation-inspector";
import { GenerationNodeCard } from "@/components/canvas/generation-node-card";
import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { PromptNodeCard } from "@/components/canvas/prompt-node-card";
import { CanvasNavigationControls } from "@/components/canvas/canvas-navigation-controls";
import { normalizeViewport } from "@/features/canvas/viewport";
import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import { deleteGraphNodes, isEditableEventTarget, selectNode } from "@/features/graph/selection";
import { appendResultNode } from "@/features/generation/result-node";
import { useGenerationJob, type PendingRef } from "@/features/generation/use-generation-job";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType } from "@/types/canvas";
import type { CanvasNodeData, ContextMenuState, Position, ViewportTransform } from "@/types/canvas";


function generationSource(prompt: string, model: string, operation: ModelOperation, position: number): CanvasNodeData {
    const video = operation.startsWith("video.");
    return { id: nanoid(), type: CanvasNodeType.Config, title: video ? "视频生成" : "图片生成", position: { x: 80 + position * 24, y: 160 + position * 24 }, width: 300, height: 140, metadata: { prompt, model, status: "loading", generationMode: video ? "video" : "image" } };
}

export default function CanvasProjectPage() {
    const { id = "" } = useParams();
    const containerRef = useRef<HTMLDivElement>(null);
    const [models, setModels] = useState<ModelSpec[]>([]);
    const [inspector, setInspector] = useState<GenerationInspectorValue>({ prompt: "", modelId: "", params: {} });
    const [operation, setOperation] = useState<ModelOperation>("image.generate");
    const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(() => new Set());
    const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
    const project = useCanvasStore((state) => state.openProject(id));
    const projectsLoaded = useCanvasStore((state) => state.projectsLoaded);
    const syncNotice = useCanvasStore((state) => state.syncNotice);
    const loadError = useCanvasStore((state) => state.loadError);
    const readOnly = Boolean(loadError?.readOnly);
    const updateProject = useCanvasStore((state) => state.updateProject);
    const viewport = normalizeViewport(project?.viewport);
    const changeViewport = useCallback((next: ViewportTransform) => {
        updateProject(id, { viewport: normalizeViewport(next) });
    }, [id, updateProject]);
    const moveNode = useCallback((nodeId: string, position: Position) => {
        if (readOnly) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const nodes = current.nodes.map((node) => node.id === nodeId ? { ...node, position } : node);
        updateProject(id, { nodes });
    }, [id, readOnly, updateProject]);

    useEffect(() => {
        setSelectedNodeIds(new Set());
        setContextMenu(null);
    }, [id]);

    useEffect(() => {
        const existing = new Set(project?.nodes.map((node) => node.id) ?? []);
        setSelectedNodeIds((current) => {
            if ([...current].every((nodeId) => existing.has(nodeId))) return current;
            return new Set([...current].filter((nodeId) => existing.has(nodeId)));
        });
    }, [project?.nodes]);

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
    const deleteNodes = useCallback((nodeIds: ReadonlySet<string>) => {
        if (readOnly || nodeIds.size === 0) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        updateProject(id, deleteGraphNodes(current.nodes, current.connections, nodeIds));
        setSelectedNodeIds((selected) => new Set([...selected].filter((nodeId) => !nodeIds.has(nodeId))));
        setContextMenu(null);
    }, [id, readOnly, updateProject]);

    useEffect(() => {
        const handleDeleteShortcut = (event: KeyboardEvent) => {
            if (readOnly || (event.key !== "Delete" && event.key !== "Backspace") || isEditableEventTarget(event.target)) return;
            if (selectedNodeIds.size === 0) return;
            event.preventDefault();
            deleteNodes(selectedNodeIds);
        };
        window.addEventListener("keydown", handleDeleteShortcut);
        return () => window.removeEventListener("keydown", handleDeleteShortcut);
    }, [deleteNodes, readOnly, selectedNodeIds]);

    const submit = (model: ModelSpec, safeParams: Record<string, unknown>) => {
        if (readOnly) return;
        const current = useCanvasStore.getState().openProject(id);
        const prompt = inspector.prompt.trim();
        if (!current || !prompt) return;
        const source = generationSource(prompt, model.model_id, operation, current.nodes.length);
        updateProject(id, { nodes: [...current.nodes, source] });
        void generation.submit({ operation, model_id: model.model_id, prompt, params: safeParams, asset_ids: [], projectId: id, sourceNodeId: source.id }).catch(() => undefined);
    };
    const addPromptNode = () => {
        if (readOnly) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const node: CanvasNodeData = {
            id: nanoid(),
            type: CanvasNodeType.Text,
            title: "提示词",
            position: { x: 80 + current.nodes.length * 24, y: 80 + current.nodes.length * 24 },
            width: 300,
            height: 250,
            metadata: {
                content: "",
                status: "idle",
                graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "prompt", text: "", outputPortId: "prompt" },
            },
        };
        updateProject(id, { nodes: [...current.nodes, node] });
        setSelectedNodeIds(new Set([node.id]));
    };

    const updatePromptNode = useCallback((nodeId: string, text: string) => {
        if (readOnly) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const nodes = current.nodes.map((node) => {
            if (node.id !== nodeId) return node;
            const graph = node.metadata?.graph;
            return {
                ...node,
                metadata: {
                    ...node.metadata,
                    content: text,
                    graph: graph?.role === "prompt"
                        ? { ...graph, text }
                        : { schemaVersion: GRAPH_SCHEMA_VERSION, role: "prompt" as const, text, outputPortId: "prompt" },
                },
            };
        });
        updateProject(id, { nodes });
    }, [id, readOnly, updateProject]);

    const openNodeContextMenu = useCallback((nodeId: string, event: ReactMouseEvent<HTMLDivElement>) => {
        if (readOnly) return;
        setSelectedNodeIds(new Set([nodeId]));
        setContextMenu({ type: "node", nodeId, x: event.clientX, y: event.clientY });
    }, [readOnly]);

    if (!project) {
        if (loadError) return <main role="alert" className="flex h-full items-center justify-center bg-[#050806] px-6 text-center text-[#ffbd73]">{loadError.message}</main>;
        if (!projectsLoaded) return <main className="flex h-full items-center justify-center bg-[#050806] text-[#829889]">正在加载画布…</main>;
        return <Navigate to="/canvas" replace />;
    }

    return <div className="flex h-full min-h-0 flex-col bg-[#050806] text-[#dceee1]">
        {loadError ? <p role="alert" className="shrink-0 border-b border-[#70502b] bg-[#241a0c] px-4 py-2 text-sm text-[#ffbd73]">{loadError.message}</p> : null}
        {syncNotice ? <p data-testid="project-sync-notice" role="status" aria-live="polite" className="shrink-0 border-b border-[#70502b] bg-[#241a0c] px-4 py-2 text-sm text-[#ffbd73]">{syncNotice}</p> : null}
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden lg:grid lg:grid-cols-[152px_minmax(0,1fr)_340px]">
            <aside data-testid="studio-palette" className="shrink-0 border-b border-[#1d3d28] bg-[#08100b] p-2 lg:border-b-0 lg:border-r lg:p-3"><div className="flex items-center justify-between gap-2 lg:block"><div><p className="px-2 text-xs tracking-[0.16em] text-[#58ed87] lg:pt-2">NODE PALETTE</p><h1 className="px-2 py-1 text-sm font-semibold lg:pb-4 lg:pt-2">{project.title}</h1></div><div className="flex flex-wrap gap-2 lg:block lg:space-y-2"><button disabled={readOnly} type="button" onClick={addPromptNode} className="flex items-center gap-2 rounded-lg border border-[#254b33] bg-[#0d1b12] px-3 py-2 text-left text-xs hover:border-[#4fbd70] disabled:cursor-not-allowed disabled:opacity-50 lg:w-full lg:py-2.5"><MessageSquareText className="size-4 text-[#58ed87]" />提示词节点</button><button disabled={readOnly} type="button" onClick={() => { setOperation("image.generate"); document.getElementById("studio-prompt")?.focus(); }} className="flex items-center gap-2 rounded-lg border border-[#254b33] bg-[#0d1b12] px-3 py-2 text-left text-xs hover:border-[#4fbd70] disabled:cursor-not-allowed disabled:opacity-50 lg:w-full lg:py-2.5"><ImagePlus className="size-4 text-[#58ed87]" />图片生成</button><button disabled={readOnly} type="button" onClick={() => { setOperation("video.generate"); document.getElementById("studio-prompt")?.focus(); }} className="flex items-center gap-2 rounded-lg border border-[#254b33] bg-[#0d1b12] px-3 py-2 text-left text-xs hover:border-[#4fbd70] disabled:cursor-not-allowed disabled:opacity-50 lg:w-full lg:py-2.5">视频生成</button></div></div><p className="mt-5 hidden px-2 text-[11px] leading-5 text-[#688371] lg:block">更多能力将按后续切片增量开放。</p></aside>
            <section data-testid="studio-canvas" className="embed-surface relative min-h-0 min-w-0 flex-1">
                <InfiniteCanvas
                    containerRef={containerRef}
                    viewport={viewport}
                    backgroundMode={project.backgroundMode}
                    onViewportChange={changeViewport}
                    onCanvasDeselect={() => {
                        setSelectedNodeIds(new Set());
                        setContextMenu(null);
                    }}
                >
                    {project.nodes.map((node) => {
                        const promptNode = node.metadata?.graph?.role === "prompt";
                        return (
                            <DraggableCanvasNode
                                key={node.id}
                                node={node}
                                scale={viewport.k}
                                selected={selectedNodeIds.has(node.id)}
                                disabled={readOnly}
                                onSelect={(nodeId, additive) => setSelectedNodeIds((current) => selectNode(current, nodeId, additive))}
                                onContextMenu={openNodeContextMenu}
                                onPositionChange={moveNode}
                            >
                                {promptNode
                                    ? <PromptNodeCard node={node} disabled={readOnly} onTextChange={(text) => updatePromptNode(node.id, text)} />
                                    : <GenerationNodeCard node={node} onRetry={readOnly ? undefined : (token) => void generation.retry(token).catch(() => undefined)} />}
                            </DraggableCanvasNode>
                        );
                    })}
                </InfiniteCanvas>
                <CanvasNavigationControls viewport={viewport} onViewportChange={changeViewport} />
                {contextMenu?.type === "node" ? (
                    <CanvasNodeContextMenu
                        menu={contextMenu}
                        onClose={() => setContextMenu(null)}
                        onDelete={() => deleteNodes(new Set([contextMenu.nodeId]))}
                    />
                ) : null}
            </section>
            <GenerationInspector models={models} operation={operation} value={inspector} disabled={readOnly || generation.state.status === "submitting"} message={generation.state.message} onChange={setInspector} onSubmit={submit} />
        </main>
    </div>;
}
