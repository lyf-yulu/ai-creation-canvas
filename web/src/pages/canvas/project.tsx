import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Navigate, useParams } from "react-router-dom";
import { ImagePlus, MessageSquareText } from "lucide-react";
import { nanoid } from "nanoid";

import type { JobState, ModelOperation, ModelSpec } from "@/api/contracts";
import { fetchModels } from "@/api/models";
import { DraggableCanvasNode } from "@/components/canvas/draggable-canvas-node";
import { ActiveConnectionPath, ConnectionPath } from "@/components/canvas/canvas-connections";
import { CanvasNodeContextMenu } from "@/components/canvas/canvas-context-menu";
import { GenerationInspector, type GenerationInspectorValue } from "@/components/canvas/generation-inspector";
import { GenerationNodeCard } from "@/components/canvas/generation-node-card";
import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { NodePort } from "@/components/canvas/node-port";
import { PromptNodeCard } from "@/components/canvas/prompt-node-card";
import { CanvasNavigationControls } from "@/components/canvas/canvas-navigation-controls";
import { normalizeViewport } from "@/features/canvas/viewport";
import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import { connectGraphPorts, getNodePorts, graphConnectionInactiveMessage, graphConnectionRejectionMessage, graphConnectionTransientKey, resolveActiveConnections, type GraphPortRef } from "@/features/graph/connect";
import { nodeRegistry } from "@/features/nodes/registry";
import { deleteGraphNodes, isEditableEventTarget, selectNode } from "@/features/graph/selection";
import { appendResultNode } from "@/features/generation/result-node";
import { useGenerationJob, type PendingRef } from "@/features/generation/use-generation-job";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { CanvasNodeType } from "@/types/canvas";
import type { CanvasNodeData, ContextMenuState, Position, ViewportTransform } from "@/types/canvas";


function generationSource(prompt: string, model: string, operation: ModelOperation, position: number): CanvasNodeData {
    const video = operation.startsWith("video.");
    return {
        id: nanoid(),
        type: CanvasNodeType.Config,
        title: video ? "视频生成" : "图片生成",
        position: { x: 80 + position * 24, y: 160 + position * 24 },
        width: 300,
        height: 140,
        metadata: {
            prompt,
            model,
            status: "loading",
            generationMode: video ? "video" : "image",
            graph: {
                schemaVersion: GRAPH_SCHEMA_VERSION,
                role: "model",
                modelId: model,
                operation,
                inputPorts: [{ id: "prompt", accepts: "prompt" }],
                outputPortId: "result",
                parameters: {},
            },
        },
    };
}

export default function CanvasProjectPage() {
    const { id = "" } = useParams();
    const containerRef = useRef<HTMLDivElement>(null);
    const contextTriggerRef = useRef<HTMLElement | SVGElement | null>(null);
    const [models, setModels] = useState<ModelSpec[]>([]);
    const [inspector, setInspector] = useState<GenerationInspectorValue>({ prompt: "", modelId: "", params: {} });
    const [operation, setOperation] = useState<ModelOperation>("image.generate");
    const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(() => new Set());
    const [selectedConnectionKey, setSelectedConnectionKey] = useState<string | null>(null);
    const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
    const [pendingPort, setPendingPortState] = useState<GraphPortRef | null>(null);
    const [connectionMessage, setConnectionMessage] = useState<string | null>(null);
    const [connectionPointerWorld, setConnectionPointerWorld] = useState<Position>({ x: 0, y: 0 });
    const [measuredNodeSizes, setMeasuredNodeSizes] = useState<Map<string, { width: number; height: number }>>(() => new Map());
    const pendingPortRef = useRef<GraphPortRef | null>(null);
    const pointerConnectionRef = useRef<number | null>(null);
    const suppressPortClickRef = useRef<string | null>(null);
    const project = useCanvasStore((state) => state.openProject(id));
    const registryRevision = useSyncExternalStore(nodeRegistry.subscribe, nodeRegistry.getSnapshot, nodeRegistry.getSnapshot);
    const projectsLoaded = useCanvasStore((state) => state.projectsLoaded);
    const syncNotice = useCanvasStore((state) => state.syncNotice);
    const loadError = useCanvasStore((state) => state.loadError);
    const readOnly = Boolean(loadError?.readOnly);
    const updateProject = useCanvasStore((state) => state.updateProject);
    const viewport = normalizeViewport(project?.viewport);
    const measuredNodeMap = useMemo(() => new Map((project?.nodes ?? []).map((node) => {
        const measured = measuredNodeSizes.get(node.id);
        return [node.id, measured ? { ...node, width: measured.width, height: measured.height } : node] as const;
    })), [measuredNodeSizes, project?.nodes]);
    const resolvedConnections = useMemo(
        () => resolveActiveConnections(project?.connections ?? [], project?.nodes ?? [], nodeRegistry),
        [project?.connections, project?.nodes, registryRevision],
    );
    const inactiveConnectionCount = resolvedConnections.filter((state) => !state.active).length;
    const recordMeasuredNodeSize = useCallback((nodeId: string, size: { width: number; height: number }) => {
        setMeasuredNodeSizes((current) => {
            const previous = current.get(nodeId);
            if (previous?.width === size.width && previous.height === size.height) return current;
            const next = new Map(current);
            next.set(nodeId, size);
            return next;
        });
    }, []);
    const setPendingPort = useCallback((port: GraphPortRef | null) => {
        pendingPortRef.current = port;
        setPendingPortState(port);
    }, []);
    const clearPendingConnection = useCallback(() => {
        pointerConnectionRef.current = null;
        suppressPortClickRef.current = null;
        setPendingPort(null);
        setConnectionMessage(null);
    }, [setPendingPort]);
    const clientToWorld = useCallback((clientX: number, clientY: number) => {
        const rect = containerRef.current?.getBoundingClientRect();
        const left = rect?.left ?? 0;
        const top = rect?.top ?? 0;
        return {
            x: (clientX - left - viewport.x) / viewport.k,
            y: (clientY - top - viewport.y) / viewport.k,
        };
    }, [viewport.k, viewport.x, viewport.y]);
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
        setSelectedConnectionKey(null);
        setContextMenu(null);
        contextTriggerRef.current = null;
        setMeasuredNodeSizes(new Map());
        clearPendingConnection();
    }, [clearPendingConnection, id]);

    useEffect(() => {
        if (!readOnly) return;
        setSelectedNodeIds(new Set());
        setSelectedConnectionKey(null);
        setContextMenu(null);
        contextTriggerRef.current = null;
        clearPendingConnection();
    }, [clearPendingConnection, readOnly]);

    useEffect(() => {
        const existing = new Set(project?.nodes.map((node) => node.id) ?? []);
        setMeasuredNodeSizes((current) => {
            if ([...current.keys()].every((nodeId) => existing.has(nodeId))) return current;
            return new Map([...current].filter(([nodeId]) => existing.has(nodeId)));
        });
        setSelectedNodeIds((current) => {
            if ([...current].every((nodeId) => existing.has(nodeId))) return current;
            return new Set([...current].filter((nodeId) => existing.has(nodeId)));
        });
    }, [project?.nodes]);

    useEffect(() => {
        if (!selectedConnectionKey || resolvedConnections.some((state) => state.connectionKey === selectedConnectionKey)) return;
        setSelectedConnectionKey(null);
    }, [resolvedConnections, selectedConnectionKey]);

    useEffect(() => {
        if (!pendingPort || pendingPort.direction !== "source") return;
        const sourceNode = project?.nodes.find((node) => node.id === pendingPort.nodeId);
        const sourceStillDeclared = sourceNode && getNodePorts(sourceNode).sources.some((port) => port.portId === pendingPort.portId);
        if (sourceStillDeclared) return;
        pointerConnectionRef.current = null;
        suppressPortClickRef.current = null;
        setPendingPort(null);
        setConnectionMessage("连接起点已失效。");
    }, [pendingPort, project?.nodes, registryRevision, setPendingPort]);

    useEffect(() => {
        const handlePointerMove = (event: PointerEvent) => {
            if (pointerConnectionRef.current !== event.pointerId) return;
            setConnectionPointerWorld(clientToWorld(event.clientX, event.clientY));
        };
        const finishOutsidePort = (event: PointerEvent) => {
            if (pointerConnectionRef.current !== event.pointerId) return;
            clearPendingConnection();
        };
        const cancel = () => clearPendingConnection();
        window.addEventListener("pointermove", handlePointerMove);
        window.addEventListener("pointerup", finishOutsidePort);
        window.addEventListener("pointercancel", finishOutsidePort);
        window.addEventListener("blur", cancel);
        return () => {
            window.removeEventListener("pointermove", handlePointerMove);
            window.removeEventListener("pointerup", finishOutsidePort);
            window.removeEventListener("pointercancel", finishOutsidePort);
            window.removeEventListener("blur", cancel);
        };
    }, [clearPendingConnection, clientToWorld]);

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
    const deleteConnection = useCallback((connectionKey: string) => {
        if (readOnly) return;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return;
        const connectionIndex = current.connections.findIndex((connection, index) => graphConnectionTransientKey(connection, index) === connectionKey);
        if (connectionIndex < 0) return;
        updateProject(id, { connections: current.connections.filter((_connection, index) => index !== connectionIndex) });
        setSelectedConnectionKey((selected) => selected === connectionKey ? null : selected);
        setContextMenu(null);
    }, [id, readOnly, updateProject]);

    useEffect(() => {
        const handleDeleteShortcut = (event: KeyboardEvent) => {
            if (readOnly || (event.key !== "Delete" && event.key !== "Backspace") || isEditableEventTarget(event.target)) return;
            if (selectedNodeIds.size === 0 && !selectedConnectionKey) return;
            event.preventDefault();
            if (selectedNodeIds.size > 0) deleteNodes(selectedNodeIds);
            else if (selectedConnectionKey) deleteConnection(selectedConnectionKey);
        };
        window.addEventListener("keydown", handleDeleteShortcut);
        return () => window.removeEventListener("keydown", handleDeleteShortcut);
    }, [deleteConnection, deleteNodes, readOnly, selectedConnectionKey, selectedNodeIds]);

    const finishPortConnection = useCallback((first: GraphPortRef, second: GraphPortRef) => {
        if (readOnly) return false;
        const current = useCanvasStore.getState().openProject(id);
        if (!current) return false;
        const result = connectGraphPorts(first, second, current.nodes, current.connections, nanoid());
        if (!result.ok) {
            setConnectionMessage(graphConnectionRejectionMessage(result.reason));
            return false;
        }
        updateProject(id, { connections: [...current.connections, result.connection] });
        setSelectedNodeIds(new Set());
        setSelectedConnectionKey(graphConnectionTransientKey(result.connection, current.connections.length));
        setPendingPort(null);
        setConnectionMessage("连接已创建。");
        return true;
    }, [id, readOnly, setPendingPort, updateProject]);

    const handlePortPointerDown = useCallback((port: GraphPortRef, event: React.PointerEvent<HTMLButtonElement>) => {
        if (readOnly || port.direction !== "source" || event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        pointerConnectionRef.current = event.pointerId;
        setConnectionMessage(null);
        setConnectionPointerWorld(clientToWorld(event.clientX, event.clientY));
        setPendingPort(port);
    }, [clientToWorld, readOnly, setPendingPort]);

    const handlePortPointerUp = useCallback((port: GraphPortRef, event: React.PointerEvent<HTMLButtonElement>) => {
        if (readOnly || pointerConnectionRef.current !== event.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        pointerConnectionRef.current = null;
        const first = pendingPortRef.current;
        if (!first || (first.nodeId === port.nodeId && first.portId === port.portId && first.direction === port.direction)) return;
        const suppressedPortKey = `${port.nodeId}\u0000${port.portId}\u0000${port.direction}`;
        suppressPortClickRef.current = suppressedPortKey;
        window.setTimeout(() => {
            if (suppressPortClickRef.current === suppressedPortKey) suppressPortClickRef.current = null;
        }, 0);
        finishPortConnection(first, port);
    }, [finishPortConnection, readOnly]);

    const handlePortClick = useCallback((port: GraphPortRef, event: React.MouseEvent<HTMLButtonElement>) => {
        if (readOnly) return;
        event.preventDefault();
        event.stopPropagation();
        const portKey = `${port.nodeId}\u0000${port.portId}\u0000${port.direction}`;
        if (suppressPortClickRef.current === portKey) {
            suppressPortClickRef.current = null;
            return;
        }
        const first = pendingPortRef.current;
        if (!first) {
            if (port.direction === "source") {
                setConnectionMessage(null);
                setPendingPort(port);
            }
            return;
        }
        if (first.nodeId === port.nodeId && first.portId === port.portId && first.direction === port.direction) return;
        finishPortConnection(first, port);
    }, [finishPortConnection, readOnly, setPendingPort]);

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

    const openNodeContextMenu = useCallback((nodeId: string, position: { x: number; y: number }, trigger: HTMLDivElement) => {
        if (readOnly) return;
        contextTriggerRef.current = trigger;
        setSelectedNodeIds(new Set([nodeId]));
        setSelectedConnectionKey(null);
        setContextMenu({ type: "node", nodeId, x: position.x, y: position.y });
    }, [readOnly]);

    const openConnectionContextMenu = useCallback((connectionId: string, connectionKey: string, position: { x: number; y: number }, trigger: SVGPathElement) => {
        if (readOnly) return;
        contextTriggerRef.current = trigger;
        setSelectedNodeIds(new Set());
        setSelectedConnectionKey(connectionKey);
        setContextMenu({ type: "connection", connectionId, connectionKey, x: position.x, y: position.y });
    }, [readOnly]);

    const closeContextMenu = useCallback((restoreFocus = false) => {
        setContextMenu(null);
        if (restoreFocus) contextTriggerRef.current?.focus();
    }, []);

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
                        setSelectedConnectionKey(null);
                        setContextMenu(null);
                        clearPendingConnection();
                    }}
                >
                    <svg className="pointer-events-none absolute left-0 top-0 z-0 overflow-visible" width="1" height="1" aria-label="画布连接">
                        {resolvedConnections.map(({ connection, connectionKey, active: connectionActive, reason }) => {
                            const from = measuredNodeMap.get(connection.fromNodeId);
                            const to = measuredNodeMap.get(connection.toNodeId);
                            if (!from || !to) return null;
                            return <ConnectionPath
                                key={connectionKey}
                                connection={connection}
                                connectionKey={connectionKey}
                                from={from}
                                to={to}
                                active={selectedConnectionKey === connectionKey}
                                enabled={connectionActive}
                                inactiveReason={reason ? graphConnectionInactiveMessage(reason) : undefined}
                                fromPortLabel={getNodePorts(from).sources.find((port) => port.portId === connection.fromPortId)?.label}
                                toPortLabel={getNodePorts(to).targets.find((port) => port.portId === connection.toPortId)?.label}
                                onSelect={() => {
                                    if (readOnly) return;
                                    setSelectedNodeIds(new Set());
                                    setSelectedConnectionKey(connectionKey);
                                    setContextMenu(null);
                                }}
                                interactive={!readOnly}
                                onOpenContextMenu={readOnly ? undefined : (position, trigger) => openConnectionContextMenu(connection.id, connectionKey, position, trigger)}
                            />;
                        })}
                        {pendingPort?.direction === "source" ? (
                            <ActiveConnectionPath
                                node={measuredNodeMap.get(pendingPort.nodeId)}
                                handle={{ nodeId: pendingPort.nodeId, handleType: "source", portId: pendingPort.portId }}
                                mouseWorld={connectionPointerWorld}
                            />
                        ) : null}
                    </svg>
                    {project.nodes.map((node) => {
                        const promptNode = node.metadata?.graph?.role === "prompt";
                        const ports = getNodePorts(node);
                        const measuredNode = measuredNodeMap.get(node.id) ?? node;
                        return (
                            <DraggableCanvasNode
                                key={node.id}
                                node={node}
                                scale={viewport.k}
                                selected={selectedNodeIds.has(node.id)}
                                disabled={readOnly}
                                onSelect={readOnly ? undefined : (nodeId, additive) => {
                                    setSelectedConnectionKey(null);
                                    setSelectedNodeIds((current) => selectNode(current, nodeId, additive));
                                }}
                                onContextMenu={readOnly ? undefined : openNodeContextMenu}
                                onPositionChange={moveNode}
                                onMeasuredSize={recordMeasuredNodeSize}
                            >
                                {promptNode
                                    ? <PromptNodeCard node={node} disabled={readOnly} onTextChange={(text) => updatePromptNode(node.id, text)} />
                                    : <GenerationNodeCard node={node} onRetry={readOnly ? undefined : (token) => void generation.retry(token).catch(() => undefined)} />}
                                {[...ports.targets, ...ports.sources].map((port) => (
                                    <NodePort
                                        key={`${port.direction}:${port.portId}`}
                                        node={measuredNode}
                                        port={port}
                                        active={pendingPort?.nodeId === port.nodeId && pendingPort.portId === port.portId && pendingPort.direction === port.direction}
                                        disabled={readOnly}
                                        onClick={handlePortClick}
                                        onPointerDown={handlePortPointerDown}
                                        onPointerUp={handlePortPointerUp}
                                    />
                                ))}
                            </DraggableCanvasNode>
                        );
                    })}
                </InfiniteCanvas>
                <CanvasNavigationControls viewport={viewport} onViewportChange={changeViewport} />
                {connectionMessage ? <p data-testid="connection-status" role="status" aria-live="polite" className="pointer-events-none absolute bottom-14 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-[#356b48] bg-[#08100b]/95 px-3 py-2 text-xs text-[#bcebc9] shadow-xl">{connectionMessage}</p> : null}
                {inactiveConnectionCount > 0 ? <p data-testid="inactive-connection-status" role="status" aria-live="polite" className="pointer-events-none absolute bottom-3 left-1/2 z-40 -translate-x-1/2 rounded border border-[#526354] bg-[#08100b]/95 px-2 py-1 text-[11px] text-[#b8cdbd]">{inactiveConnectionCount} 条连接暂不可用，已保留在画布中。</p> : null}
                {contextMenu?.type === "node" ? (
                    <CanvasNodeContextMenu
                        menu={contextMenu}
                        onClose={closeContextMenu}
                        onDelete={() => deleteNodes(new Set([contextMenu.nodeId]))}
                    />
                ) : contextMenu?.type === "connection" ? (
                    <CanvasNodeContextMenu
                        menu={contextMenu}
                        onClose={closeContextMenu}
                        onDelete={() => deleteConnection(contextMenu.connectionKey)}
                    />
                ) : null}
            </section>
            <GenerationInspector models={models} operation={operation} value={inspector} disabled={readOnly || generation.state.status === "submitting"} message={generation.state.message} onChange={setInspector} onSubmit={submit} />
        </main>
    </div>;
}
