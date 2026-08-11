import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraggableCanvasNode } from "@/components/canvas/draggable-canvas-node";
import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setScopedStoreFactoryForTest, setStorageScope } from "@/storage/scope";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

const modelResponse = new Response(JSON.stringify({ models: [] }), {
    headers: { "content-type": "application/json" },
});

function node(id: string, x: number): CanvasNodeData {
    return {
        id,
        type: CanvasNodeType.Text,
        title: `Prompt ${id}`,
        position: { x, y: 80 },
        width: 280,
        height: 160,
        metadata: {
            content: id,
            status: "idle",
            graph: { schemaVersion: 1, role: "prompt", text: id, outputPortId: "prompt" },
        },
    };
}

async function renderProject(nodes: CanvasNodeData[] = [], connections: CanvasConnection[] = []) {
    await setStorageScope({ environment: "test", userId: "editing-user" });
    const projectId = useCanvasStore.getState().createProject("Editing Canvas");
    useCanvasStore.getState().updateProject(projectId, { nodes, connections });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(modelResponse.clone()));
    render(
        <MemoryRouter initialEntries={[`/canvas/${projectId}`]}>
            <Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes>
        </MemoryRouter>,
    );
    return projectId;
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

describe("project-scoped node selection and deletion", () => {
    it("selects one node, adds another with a modifier, and clears selection on a background click", async () => {
        await renderProject([node("a", 80), node("b", 400)]);

        fireEvent.pointerDown(screen.getByTestId("draggable-node-a"), { button: 0, pointerId: 1 });
        fireEvent.pointerUp(window, { pointerId: 1 });
        expect(screen.getByTestId("draggable-node-a")).toHaveAttribute("aria-selected", "true");
        expect(screen.getByTestId("draggable-node-b")).toHaveAttribute("aria-selected", "false");

        fireEvent.pointerDown(screen.getByTestId("draggable-node-b"), { button: 0, pointerId: 2, metaKey: true });
        expect(screen.getByTestId("draggable-node-a")).toHaveAttribute("aria-selected", "true");
        expect(screen.getByTestId("draggable-node-b")).toHaveAttribute("aria-selected", "true");

        fireEvent.pointerDown(screen.getByTestId("infinite-canvas"), { button: 0, pointerId: 3 });
        fireEvent.pointerUp(window, { pointerId: 3 });
        expect(screen.getByTestId("draggable-node-a")).toHaveAttribute("aria-selected", "false");
        expect(screen.getByTestId("draggable-node-b")).toHaveAttribute("aria-selected", "false");
    });

    it("deletes all selected nodes and their incident connections with Delete", async () => {
        const projectId = await renderProject([node("a", 80), node("b", 400), node("c", 720)], [
            { id: "a-b", fromNodeId: "a", fromPortId: "prompt", toNodeId: "b", toPortId: "prompt" },
            { id: "c-b", fromNodeId: "c", fromPortId: "prompt", toNodeId: "b", toPortId: "prompt" },
        ]);
        fireEvent.pointerDown(screen.getByTestId("draggable-node-a"), { button: 0, pointerId: 1 });
        fireEvent.pointerDown(screen.getByTestId("draggable-node-b"), { button: 0, pointerId: 2, ctrlKey: true });

        fireEvent.keyDown(window, { key: "Delete" });

        expect(useCanvasStore.getState().openProject(projectId)?.nodes.map((item) => item.id)).toEqual(["c"]);
        expect(useCanvasStore.getState().openProject(projectId)?.connections).toEqual([]);
    });

    it("does not delete a selected node while any editable control owns the key event", async () => {
        const projectId = await renderProject([node("a", 80)]);
        fireEvent.pointerDown(screen.getByTestId("draggable-node-a"), { button: 0, pointerId: 1 });
        const contentEditor = document.createElement("div");
        contentEditor.setAttribute("contenteditable", "true");
        document.body.append(contentEditor);
        const controls = [
            screen.getByLabelText("提示词"),
            screen.getByLabelText("模型"),
            screen.getByLabelText("导入 TXT"),
            contentEditor,
        ];

        for (const control of controls) fireEvent.keyDown(control, { key: "Backspace" });

        expect(useCanvasStore.getState().openProject(projectId)?.nodes.map((item) => item.id)).toEqual(["a"]);
        contentEditor.remove();
    });

    it("deletes a node and its incident connection from the node context menu", async () => {
        const projectId = await renderProject([node("a", 80), node("b", 400)], [
            { id: "a-b", fromNodeId: "a", fromPortId: "prompt", toNodeId: "b", toPortId: "prompt" },
        ]);

        fireEvent.contextMenu(screen.getByTestId("draggable-node-a"), { clientX: 42, clientY: 64 });
        fireEvent.click(screen.getByRole("button", { name: "删除" }));

        expect(useCanvasStore.getState().openProject(projectId)?.nodes.map((item) => item.id)).toEqual(["b"]);
        expect(useCanvasStore.getState().openProject(projectId)?.connections).toEqual([]);
    });

    it("keeps the node context menu inside a narrow viewport", async () => {
        await renderProject([node("a", 80)]);
        vi.stubGlobal("innerWidth", 360);
        vi.stubGlobal("innerHeight", 480);

        fireEvent.contextMenu(screen.getByTestId("draggable-node-a"), { clientX: 900, clientY: 900 });

        const menu = screen.getByRole("button", { name: "删除" }).parentElement!;
        expect(Number.parseFloat(menu.style.left)).toBeLessThanOrEqual(176);
        expect(Number.parseFloat(menu.style.top)).toBeLessThanOrEqual(384);
    });
});

describe("prompt node editing", () => {
    it("creates one blank editable prompt without starting a job or showing a spinner", async () => {
        const projectId = await renderProject();
        fireEvent.click(screen.getByRole("button", { name: "提示词节点" }));

        const editor = screen.getByRole("textbox", { name: "提示词内容" });
        expect(editor).toHaveValue("");
        expect(screen.queryByTestId(/generation-node-/)).not.toBeInTheDocument();
        expect(document.querySelector(".animate-spin")).not.toBeInTheDocument();
        const prompt = useCanvasStore.getState().openProject(projectId)?.nodes[0];
        expect(prompt?.metadata?.graph).toMatchObject({ role: "prompt", text: "" });
        expect((fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) => String(url) === "/api/v1/jobs")).toHaveLength(0);

        fireEvent.change(editor, { target: { value: "雾中的未来城市" } });
        expect(useCanvasStore.getState().openProject(projectId)?.nodes[0].metadata?.graph).toMatchObject({ role: "prompt", text: "雾中的未来城市" });
    });

    it("imports a local UTF-8 txt file into the same persisted prompt field", async () => {
        const projectId = await renderProject();
        fireEvent.click(screen.getByRole("button", { name: "提示词节点" }));
        const file = new File(["第一幕：绿色雨夜"], "prompt.txt", { type: "text/plain" });

        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [file] } });

        await waitFor(() => expect(screen.getByRole("textbox", { name: "提示词内容" })).toHaveValue("第一幕：绿色雨夜"));
        expect(useCanvasStore.getState().openProject(projectId)?.nodes[0].metadata?.graph).toMatchObject({ role: "prompt", text: "第一幕：绿色雨夜" });
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it.each([
        ["wrong type", new File(["hello"], "prompt.md", { type: "text/markdown" }), /TXT/],
        ["oversized", new File([new Uint8Array(1_048_577)], "large.txt", { type: "text/plain" }), /1 MB/],
        ["invalid UTF-8", new File([new Uint8Array([0xc3, 0x28])], "broken.txt", { type: "text/plain" }), /UTF-8/],
    ])("shows a visible error for %s imports without replacing text", async (_name, file, message) => {
        await renderProject();
        fireEvent.click(screen.getByRole("button", { name: "提示词节点" }));
        const editor = screen.getByRole("textbox", { name: "提示词内容" });
        fireEvent.change(editor, { target: { value: "keep me" } });

        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [file] } });

        expect(await screen.findByRole("alert")).toHaveTextContent(message);
        expect(editor).toHaveValue("keep me");
    });
});

it("keeps prompt editing and deletion disabled when graph loading entered read-only protection", async () => {
    const projectId = await renderProject([node("protected", 80)]);
    useCanvasStore.setState({
        loadError: { code: "UNSUPPORTED_GRAPH_SCHEMA", message: "需要升级应用", readOnly: true },
    });
    fireEvent.pointerDown(screen.getByTestId("draggable-node-protected"), { button: 0, pointerId: 1 });
    const editor = screen.getByRole("textbox", { name: "提示词内容" });

    expect(editor).toBeDisabled();
    fireEvent.change(editor, { target: { value: "must not persist" } });
    fireEvent.keyDown(window, { key: "Delete" });
    fireEvent.contextMenu(screen.getByTestId("draggable-node-protected"));

    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(useCanvasStore.getState().openProject(projectId)?.nodes[0].metadata?.graph).toMatchObject({ role: "prompt", text: "protected" });
});

it("does not drag from contenteditable descendants", () => {
    const onPositionChange = vi.fn();
    render(
        <DraggableCanvasNode node={node("editable", 80)} scale={1} onPositionChange={onPositionChange}>
            <div contentEditable suppressContentEditableWarning>editable text</div>
        </DraggableCanvasNode>,
    );
    const editor = screen.getByText("editable text");

    fireEvent.pointerDown(editor, { button: 0, pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 50, clientY: 50 });
    fireEvent.pointerUp(window, { pointerId: 1 });

    expect(onPositionChange).not.toHaveBeenCalled();
});
