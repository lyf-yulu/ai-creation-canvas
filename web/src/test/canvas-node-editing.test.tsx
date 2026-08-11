import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraggableCanvasNode } from "@/components/canvas/draggable-canvas-node";
import { PromptNodeCard } from "@/components/canvas/prompt-node-card";
import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { clearStorageScope, setScopedStoreFactoryForTest, setStorageScope } from "@/storage/scope";
import { CanvasNodeType, type CanvasConnection, type CanvasNodeData } from "@/types/canvas";

const modelResponse = new Response(JSON.stringify({ models: [] }), {
    headers: { "content-type": "application/json" },
});

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function fileWithRead(name: string, read: Promise<ArrayBuffer>) {
    const file = new File(["placeholder"], name, { type: "text/plain" });
    Object.defineProperty(file, "arrayBuffer", { configurable: true, value: () => read });
    return file;
}

function utf8(value: string) {
    return new TextEncoder().encode(value).buffer;
}

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

    it("selects an interactive control's node without dragging or deleting the prior selection", async () => {
        const projectId = await renderProject([node("a", 80), node("b", 400)]);
        const first = screen.getByTestId("draggable-node-a");
        const second = screen.getByTestId("draggable-node-b");
        fireEvent.pointerDown(first, { button: 0, pointerId: 1 });
        fireEvent.pointerUp(window, { pointerId: 1 });
        const editor = within(second).getByRole("textbox", { name: "提示词内容" });

        fireEvent.focus(editor);
        expect(first).toHaveAttribute("aria-selected", "false");
        expect(second).toHaveAttribute("aria-selected", "true");
        fireEvent.pointerDown(first, { button: 0, pointerId: 3 });
        fireEvent.pointerUp(window, { pointerId: 3 });

        fireEvent.pointerDown(editor, { button: 0, pointerId: 2, clientX: 410, clientY: 100 });
        fireEvent.focus(editor);
        fireEvent.pointerMove(window, { pointerId: 2, clientX: 510, clientY: 200 });
        fireEvent.pointerUp(window, { pointerId: 2 });

        expect(first).toHaveAttribute("aria-selected", "false");
        expect(second).toHaveAttribute("aria-selected", "true");
        expect(useCanvasStore.getState().openProject(projectId)?.nodes.find((item) => item.id === "b")?.position).toEqual({ x: 400, y: 80 });
        fireEvent.keyDown(editor, { key: "Delete" });
        expect(useCanvasStore.getState().openProject(projectId)?.nodes.map((item) => item.id)).toEqual(["a", "b"]);

        second.focus();
        fireEvent.keyDown(second, { key: "Delete" });
        expect(useCanvasStore.getState().openProject(projectId)?.nodes.map((item) => item.id)).toEqual(["a"]);
    });

    it("deletes a node and its incident connection from the node context menu", async () => {
        const projectId = await renderProject([node("a", 80), node("b", 400)], [
            { id: "a-b", fromNodeId: "a", fromPortId: "prompt", toNodeId: "b", toPortId: "prompt" },
        ]);

        fireEvent.contextMenu(screen.getByTestId("draggable-node-a"), { clientX: 42, clientY: 64 });
        fireEvent.click(screen.getByRole("menuitem", { name: "删除" }));

        expect(useCanvasStore.getState().openProject(projectId)?.nodes.map((item) => item.id)).toEqual(["b"]);
        expect(useCanvasStore.getState().openProject(projectId)?.connections).toEqual([]);
    });

    it("keeps the node context menu inside a narrow viewport", async () => {
        await renderProject([node("a", 80)]);
        vi.stubGlobal("innerWidth", 360);
        vi.stubGlobal("innerHeight", 480);

        fireEvent.contextMenu(screen.getByTestId("draggable-node-a"), { clientX: 900, clientY: 900 });

        const menu = screen.getByRole("menuitem", { name: "删除" }).parentElement!;
        expect(Number.parseFloat(menu.style.left)).toBeLessThanOrEqual(176);
        expect(Number.parseFloat(menu.style.top)).toBeLessThanOrEqual(384);
    });

    it("remeasures menu height on resize instead of relying on a fixed item count", async () => {
        await renderProject([node("a", 80)]);
        vi.stubGlobal("innerWidth", 240);
        vi.stubGlobal("innerHeight", 240);
        let menuHeight = 80;
        const nativeRect = HTMLElement.prototype.getBoundingClientRect;
        vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
            if (this.getAttribute("role") === "menu") {
                return { x: 0, y: 0, left: 0, top: 0, right: 100, bottom: menuHeight, width: 100, height: menuHeight, toJSON: () => ({}) } as DOMRect;
            }
            return nativeRect.call(this);
        });

        fireEvent.contextMenu(screen.getByTestId("draggable-node-a"), { clientX: 230, clientY: 230 });
        const menu = screen.getByRole("menu", { name: "节点操作" });
        await waitFor(() => expect(menu.style.top).toBe("152px"));

        menuHeight = 180;
        fireEvent(window, new Event("resize"));
        await waitFor(() => expect(menu.style.top).toBe("52px"));
    });

    it.each([
        ["ContextMenu", { key: "ContextMenu" }],
        ["Shift+F10", { key: "F10", shiftKey: true }],
    ])("opens an accessible menu with %s and restores node focus on Escape", async (_name, shortcut) => {
        await renderProject([node("a", 80)]);
        const trigger = screen.getByTestId("draggable-node-a");
        trigger.focus();

        fireEvent.keyDown(trigger, shortcut);

        expect(screen.getByRole("menu", { name: "节点操作" })).toBeVisible();
        const deleteItem = screen.getByRole("menuitem", { name: "删除" });
        await waitFor(() => expect(deleteItem).toHaveFocus());
        fireEvent.keyDown(deleteItem, { key: "Escape" });
        expect(screen.queryByRole("menu")).not.toBeInTheDocument();
        expect(trigger).toHaveFocus();
    });

    it("closes the context menu on Tab and on an outside pointer without trapping focus", async () => {
        await renderProject([node("a", 80)]);
        const trigger = screen.getByTestId("draggable-node-a");
        trigger.focus();
        fireEvent.keyDown(trigger, { key: "ContextMenu" });
        const item = await screen.findByRole("menuitem", { name: "删除" });

        fireEvent.keyDown(item, { key: "Tab" });
        expect(screen.queryByRole("menu")).not.toBeInTheDocument();

        fireEvent.keyDown(trigger, { key: "ContextMenu" });
        expect(screen.getByRole("menu")).toBeInTheDocument();
        fireEvent.pointerDown(document.body);
        expect(screen.queryByRole("menu")).not.toBeInTheDocument();
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

    it("keeps the newest successful import when an older import later fails", async () => {
        const first = deferred<ArrayBuffer>();
        const second = deferred<ArrayBuffer>();
        const onTextChange = vi.fn();
        render(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);

        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("a.txt", first.promise)] } });
        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("b.txt", second.promise)] } });
        await act(async () => second.resolve(utf8("newest")));
        expect(onTextChange).toHaveBeenCalledTimes(1);
        expect(onTextChange).toHaveBeenLastCalledWith("newest");

        await act(async () => first.reject(new Error("old read failed")));
        expect(onTextChange).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("keeps the newest import error when an older successful read finishes later", async () => {
        const first = deferred<ArrayBuffer>();
        const second = deferred<ArrayBuffer>();
        const onTextChange = vi.fn();
        render(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);

        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("a.txt", first.promise)] } });
        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("b.txt", second.promise)] } });
        await act(async () => second.resolve(new Uint8Array([0xc3, 0x28]).buffer));
        expect(screen.getByRole("alert")).toHaveTextContent("UTF-8");

        await act(async () => first.resolve(utf8("stale success")));
        expect(onTextChange).not.toHaveBeenCalled();
        expect(screen.getByRole("alert")).toHaveTextContent("UTF-8");
    });

    it("does not publish a pending import after the prompt node identity changes", async () => {
        const pending = deferred<ArrayBuffer>();
        const onTextChange = vi.fn();
        const view = render(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);
        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("a.txt", pending.promise)] } });

        view.rerender(<PromptNodeCard node={node("b", 80)} onTextChange={onTextChange} />);
        await act(async () => pending.resolve(utf8("wrong node")));

        expect(onTextChange).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("does not publish or set an error after unmounting with a read in flight", async () => {
        const pending = deferred<ArrayBuffer>();
        const onTextChange = vi.fn();
        const view = render(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);
        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("a.txt", pending.promise)] } });
        view.unmount();

        await act(async () => pending.reject(new Error("after unmount")));

        expect(onTextChange).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("invalidates a successful read when disabled and never revives it after re-enable", async () => {
        const pending = deferred<ArrayBuffer>();
        const onTextChange = vi.fn();
        const view = render(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);
        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("a.txt", pending.promise)] } });

        view.rerender(<PromptNodeCard disabled node={node("a", 80)} onTextChange={onTextChange} />);
        view.rerender(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);
        await act(async () => pending.resolve(utf8("must stay stale")));

        expect(onTextChange).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("does not publish a read error after the prompt becomes disabled", async () => {
        const pending = deferred<ArrayBuffer>();
        const onTextChange = vi.fn();
        const view = render(<PromptNodeCard node={node("a", 80)} onTextChange={onTextChange} />);
        fireEvent.change(screen.getByLabelText("导入 TXT"), { target: { files: [fileWithRead("a.txt", pending.promise)] } });

        view.rerender(<PromptNodeCard disabled node={node("a", 80)} onTextChange={onTextChange} />);
        await act(async () => pending.reject(new Error("disabled read")));

        expect(onTextChange).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
});

it("keeps prompt editing and deletion disabled when graph loading entered read-only protection", async () => {
    const projectId = await renderProject([node("protected", 80)]);
    useCanvasStore.setState({
        loadError: { code: "UNSUPPORTED_GRAPH_SCHEMA", message: "需要升级应用", readOnly: true },
    });
    fireEvent.pointerDown(screen.getByTestId("draggable-node-protected"), { button: 0, pointerId: 1 });
    fireEvent.keyDown(screen.getByTestId("draggable-node-protected"), { key: "Enter" });
    const editor = screen.getByRole("textbox", { name: "提示词内容" });

    expect(editor).toBeDisabled();
    fireEvent.change(editor, { target: { value: "must not persist" } });
    fireEvent.keyDown(window, { key: "Delete" });
    fireEvent.contextMenu(screen.getByTestId("draggable-node-protected"));

    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByTestId("draggable-node-protected")).toHaveAttribute("aria-selected", "false");
    expect(useCanvasStore.getState().openProject(projectId)?.nodes[0].metadata?.graph).toMatchObject({ role: "prompt", text: "protected" });
});

it("preserves native context menus for editable descendants and read-only nodes", () => {
    const onContextMenu = vi.fn();
    const onSelect = vi.fn();
    const view = render(
        <DraggableCanvasNode node={node("editable", 80)} scale={1} onPositionChange={vi.fn()} onContextMenu={onContextMenu} onSelect={onSelect}>
            <textarea aria-label="node editor" />
        </DraggableCanvasNode>,
    );

    expect(fireEvent.contextMenu(screen.getByRole("textbox", { name: "node editor" }))).toBe(true);
    expect(onContextMenu).not.toHaveBeenCalled();

    view.rerender(
        <DraggableCanvasNode disabled node={node("editable", 80)} scale={1} onPositionChange={vi.fn()} onContextMenu={onContextMenu} onSelect={onSelect}>
            <span>read only surface</span>
        </DraggableCanvasNode>,
    );
    expect(fireEvent.contextMenu(screen.getByText("read only surface"))).toBe(true);
    fireEvent.pointerDown(screen.getByTestId("draggable-node-editable"), { button: 0, pointerId: 2 });
    fireEvent.keyDown(screen.getByTestId("draggable-node-editable"), { key: "Enter" });
    expect(onContextMenu).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
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
