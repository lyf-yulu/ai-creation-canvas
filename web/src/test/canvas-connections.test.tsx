import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GRAPH_SCHEMA_VERSION } from "@/features/graph/contracts";
import { connectGraphPorts, getNodePorts, type GraphPortRef } from "@/features/graph/connect";
import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore, type CanvasProject } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setScopedStoreFactoryForTest, setStorageScope } from "@/storage/scope";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

function baseNode(id: string, type: CanvasNodeData["type"], x: number, y: number, metadata: CanvasNodeData["metadata"]): CanvasNodeData {
    return { id, type, title: id, position: { x, y }, width: 240, height: 160, metadata };
}

function promptNode(id: string, x = 40, y = 50) {
    return baseNode(id, CanvasNodeType.Text, x, y, {
        content: id,
        status: "idle",
        graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "prompt", text: id, outputPortId: "prompt" },
    });
}

function modelNode(id: string, inputPortIds: string[], x = 420, y = 80) {
    return baseNode(id, CanvasNodeType.Config, x, y, {
        status: "idle",
        graph: {
            schemaVersion: GRAPH_SCHEMA_VERSION,
            role: "model",
            modelId: "declared-model",
            operation: "video.generate",
            inputPortIds,
            outputPortId: "result",
            parameters: {},
        },
    });
}

function mediaNode(id: string, mediaType: "image" | "video" | "audio", x = 40, y = 280) {
    const type = mediaType === "image" ? CanvasNodeType.Image : mediaType === "video" ? CanvasNodeType.Video : CanvasNodeType.Audio;
    return baseNode(id, type, x, y, {
        status: "success",
        content: mediaType === "image" ? "/api/v1/results/image" : undefined,
        graph: { schemaVersion: GRAPH_SCHEMA_VERSION, role: "result", mediaType, outputPortId: "media", assetId: `${id}-asset` },
    });
}

function port(nodeId: string, portId: string, direction: GraphPortRef["direction"]): GraphPortRef {
    return { nodeId, portId, direction };
}

async function renderProject(nodes: CanvasNodeData[], connections: CanvasConnection[] = [], viewport = { x: 0, y: 0, k: 1 }) {
    await setStorageScope({ environment: "test", userId: "connection-user" });
    const projectId = useCanvasStore.getState().createProject("Connection Canvas");
    useCanvasStore.getState().updateProject(projectId, { nodes, connections, viewport });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ models: [] }), { headers: { "content-type": "application/json" } })));
    const view = render(
        <MemoryRouter initialEntries={[`/canvas/${projectId}`]}>
            <Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes>
        </MemoryRouter>,
    );
    return { projectId, ...view };
}

beforeEach(() => {
    useCanvasStore.setState({
        projects: [],
        projectSyncMetadata: {},
        syncNotice: null,
        loadError: null,
        hydrated: true,
        projectsLoaded: true,
    });
});

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    clearStorageScope();
    setScopedStoreFactoryForTest();
});

describe("named-port graph rules", () => {
    it("exposes only stable ports declared by graph metadata", () => {
        const model = modelNode("model", ["prompt", "first_frame", "reference_audio"]);

        expect(getNodePorts(model)).toEqual({
            sources: [{ nodeId: "model", portId: "result", direction: "source", mediaType: undefined }],
            targets: [
                { nodeId: "model", portId: "prompt", direction: "target", mediaType: undefined },
                { nodeId: "model", portId: "first_frame", direction: "target", mediaType: "image" },
                { nodeId: "model", portId: "reference_audio", direction: "target", mediaType: "audio" },
            ],
        });
    });

    it("accepts compatible named ports and rejects self, duplicate, incompatible and second-prompt edges", () => {
        const nodes = [promptNode("prompt-a"), promptNode("prompt-b"), mediaNode("image", "image"), modelNode("model", ["prompt", "first_frame", "reference_audio"])];
        const accepted = connectGraphPorts(port("prompt-a", "prompt", "source"), port("model", "prompt", "target"), nodes, [], "edge-prompt");
        expect(accepted).toEqual({
            ok: true,
            connection: { id: "edge-prompt", fromNodeId: "prompt-a", fromPortId: "prompt", toNodeId: "model", toPortId: "prompt" },
        });
        const existing = accepted.ok ? [accepted.connection] : [];

        expect(connectGraphPorts(port("model", "result", "source"), port("model", "prompt", "target"), nodes, existing, "self")).toEqual({ ok: false, reason: "self" });
        expect(connectGraphPorts(port("prompt-a", "prompt", "source"), port("model", "prompt", "target"), nodes, existing, "duplicate")).toEqual({ ok: false, reason: "duplicate" });
        expect(connectGraphPorts(port("image", "media", "source"), port("model", "reference_audio", "target"), nodes, existing, "wrong-media")).toEqual({ ok: false, reason: "incompatible" });
        expect(connectGraphPorts(port("prompt-b", "prompt", "source"), port("model", "prompt", "target"), nodes, existing, "second-prompt")).toEqual({ ok: false, reason: "prompt-occupied" });
        expect(connectGraphPorts(port("image", "media", "source"), port("model", "first_frame", "target"), nodes, existing, "first-frame")).toMatchObject({ ok: true });
    });
});

describe("canvas named-port interactions", () => {
    it("connects accessible port buttons by click and preserves named ports after a reload-shaped rehydrate", async () => {
        const prompt = promptNode("prompt");
        prompt.title = "故事文本";
        const model = modelNode("model", ["prompt"]);
        model.title = "视频模型";
        const { projectId, unmount } = await renderProject([prompt, model]);
        const source = screen.getByRole("button", { name: "故事文本：提示词输出端口" });
        const target = screen.getByRole("button", { name: "视频模型：提示词输入端口" });

        source.focus();
        fireEvent.click(source);
        expect(source).toHaveAttribute("aria-pressed", "true");
        target.focus();
        fireEvent.click(target);

        const saved = useCanvasStore.getState().openProject(projectId)!;
        expect(saved.connections).toEqual([
            expect.objectContaining({ fromNodeId: "prompt", fromPortId: "prompt", toNodeId: "model", toPortId: "prompt" }),
        ]);

        const serialized = JSON.parse(JSON.stringify(saved)) as CanvasProject;
        unmount();
        useCanvasStore.getState().replaceProjects([serialized]);
        render(
            <MemoryRouter initialEntries={[`/canvas/${projectId}`]}>
                <Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes>
            </MemoryRouter>,
        );
        expect(document.querySelectorAll("[data-connection-id]")).toHaveLength(1);
        expect(useCanvasStore.getState().openProject(projectId)?.connections[0]).toMatchObject({ fromPortId: "prompt", toPortId: "prompt" });
    });

    it("connects a media output with a pointer gesture without dragging nodes or swallowing the next keyboard click", async () => {
        const image = mediaNode("image", "image", 60, 260);
        image.title = "图片结果";
        const model = modelNode("model", ["first_frame"], 460, 100);
        model.title = "视频模型";
        const { projectId } = await renderProject([image, model]);
        const source = screen.getByRole("button", { name: "图片结果：媒体输出端口" });
        const target = screen.getByRole("button", { name: "视频模型：首帧输入端口" });

        fireEvent.pointerDown(source, { button: 0, pointerId: 19, clientX: 300, clientY: 340 });
        fireEvent.pointerMove(window, { pointerId: 19, clientX: 460, clientY: 150 });
        fireEvent.pointerUp(target, { button: 0, pointerId: 19, clientX: 460, clientY: 150 });

        const saved = useCanvasStore.getState().openProject(projectId)!;
        expect(saved.connections[0]).toMatchObject({ fromNodeId: "image", fromPortId: "media", toNodeId: "model", toPortId: "first_frame" });
        expect(saved.nodes.map((node) => node.position)).toEqual([{ x: 60, y: 260 }, { x: 460, y: 100 }]);

        useCanvasStore.getState().updateProject(projectId, { connections: [] });
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        fireEvent.click(source);
        fireEvent.click(target);
        expect(useCanvasStore.getState().openProject(projectId)?.connections).toHaveLength(1);
    });

    it("renders world-coordinate edge geometry once under pan and zoom", async () => {
        const prompt = promptNode("prompt", 40, 50);
        const model = modelNode("model", ["prompt"], 420, 80);
        const connection: CanvasConnection = { id: "edge", fromNodeId: "prompt", fromPortId: "prompt", toNodeId: "model", toPortId: "prompt" };
        await renderProject([prompt, model], [connection], { x: 120, y: -30, k: 2 });

        expect(screen.getByTestId("canvas-world")).toHaveStyle({ transform: "translate(120px, -30px) scale(2)" });
        const path = document.querySelector<SVGPathElement>("[data-connection-id='edge']");
        expect(path).toHaveAttribute("d", "M 280 130 C 350 130, 350 160, 420 160");
    });

    it("selects and deletes an edge with the keyboard, and deletes another from its context menu", async () => {
        const prompt = promptNode("prompt");
        const model = modelNode("model", ["prompt", "first_frame"]);
        const image = mediaNode("image", "image");
        const edges: CanvasConnection[] = [
            { id: "prompt-edge", fromNodeId: "prompt", fromPortId: "prompt", toNodeId: "model", toPortId: "prompt" },
            { id: "image-edge", fromNodeId: "image", fromPortId: "media", toNodeId: "model", toPortId: "first_frame" },
        ];
        const { projectId } = await renderProject([prompt, model, image], edges);
        const promptEdge = document.querySelector<SVGPathElement>("[data-connection-id='prompt-edge']")!;

        fireEvent.click(promptEdge);
        expect(promptEdge).toHaveAttribute("aria-selected", "true");
        fireEvent.keyDown(window, { key: "Delete" });
        expect(useCanvasStore.getState().openProject(projectId)?.connections.map((edge) => edge.id)).toEqual(["image-edge"]);

        const imageEdge = document.querySelector<SVGPathElement>("[data-connection-id='image-edge']")!;
        fireEvent.contextMenu(imageEdge, { clientX: 40, clientY: 70 });
        expect(screen.getByRole("menu", { name: "连接操作" })).toBeVisible();
        fireEvent.click(screen.getByRole("menuitem", { name: "删除" }));
        expect(useCanvasStore.getState().openProject(projectId)?.connections).toEqual([]);
    });

    it("does not expose an enabled connection gesture in read-only mode", async () => {
        const prompt = promptNode("prompt");
        prompt.title = "只读提示词";
        const model = modelNode("model", ["prompt"]);
        model.title = "只读模型";
        const { projectId } = await renderProject([prompt, model]);
        useCanvasStore.setState({ loadError: { code: "CANVAS_LOAD_FAILED", message: "只读保护", readOnly: true } });

        const source = await screen.findByRole("button", { name: "只读提示词：提示词输出端口" });
        const target = screen.getByRole("button", { name: "只读模型：提示词输入端口" });
        expect(source).toBeDisabled();
        expect(target).toBeDisabled();
        fireEvent.click(source);
        fireEvent.click(target);
        expect(useCanvasStore.getState().openProject(projectId)?.connections).toEqual([]);
    });
});
