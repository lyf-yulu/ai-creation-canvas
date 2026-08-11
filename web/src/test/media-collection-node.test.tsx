import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { setCsrfToken } from "@/api/client";
import { fetchAsset, uploadMediaAsset } from "@/api/assets";
import type { OwnedMediaAsset } from "@/api/contracts";
import { MediaCollectionNode } from "@/components/canvas/media-collection-node";
import { mediaItemLabel, moveMediaItem, safeMediaDisplayName } from "@/features/graph/media-collection";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import type { GraphMediaItem, GraphMediaType } from "@/features/graph/contracts";
import CanvasProjectPage from "@/pages/canvas/project";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";

const items: GraphMediaItem[] = [
    { id: "item-a", assetId: "asset-a", displayName: "一.png", mimeType: "image/png", bytes: 20 },
    { id: "item-b", assetId: "asset-b", displayName: "二.png", mimeType: "image/png", bytes: 30 },
    { id: "item-c", assetId: "asset-c", displayName: "三.png", mimeType: "image/png", bytes: 40 },
];

function collectionNode(mediaType: GraphMediaType = "image", collectionItems: GraphMediaItem[] = items): CanvasNodeData {
    const type = mediaType === "image" ? CanvasNodeType.Image : mediaType === "video" ? CanvasNodeType.Video : CanvasNodeType.Audio;
    return {
        id: `${mediaType}-collection`,
        type,
        title: mediaType === "image" ? "参考图片" : mediaType === "video" ? "参考视频" : "参考音频",
        position: { x: 10, y: 20 },
        width: 360,
        height: 260,
        metadata: {
            graph: { schemaVersion: 1, role: "media-collection", mediaType, outputPortId: "media", items: collectionItems },
        },
    };
}

afterEach(() => {
    cleanup();
    setCsrfToken(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    useCanvasStore.setState({ projects: [], projectSyncMetadata: {}, syncNotice: null, loadError: null, hydrated: true, projectsLoaded: true });
});

it("derives stable numbered labels from the persisted order", () => {
    expect(mediaItemLabel("image", 0)).toBe("图片1");
    expect(mediaItemLabel("video", 14)).toBe("视频15");
    expect(mediaItemLabel("audio", 2)).toBe("音频3");
    expect(moveMediaItem(items, "item-c", -1).map((item) => item.assetId)).toEqual(["asset-a", "asset-c", "asset-b"]);
    expect(moveMediaItem(items, "missing", -1)).toBe(items);
    expect(safeMediaDisplayName("../../private\\frame\u0000.png", "image")).toBe("frame.png");
});

it("previews, removes, drags, and keyboard-reorders one ordered image collection", () => {
    const changes: GraphMediaItem[][] = [];
    const { rerender } = render(<MediaCollectionNode node={collectionNode()} onItemsChange={(next) => changes.push(next)} />);

    expect(screen.getByRole("img", { name: "图片1 一.png" })).toHaveAttribute("src", "/api/v1/assets/asset-a/content");
    expect(screen.getByText("图片2")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "上移 图片3" }));
    expect(changes.at(-1)?.map((item) => item.assetId)).toEqual(["asset-a", "asset-c", "asset-b"]);

    rerender(<MediaCollectionNode node={collectionNode("image", changes.at(-1))} onItemsChange={(next) => changes.push(next)} />);
    fireEvent.dragStart(screen.getByTestId("media-item-item-a"));
    fireEvent.dragOver(screen.getByTestId("media-item-item-b"));
    fireEvent.drop(screen.getByTestId("media-item-item-b"));
    expect(changes.at(-1)?.map((item) => item.assetId)).toEqual(["asset-c", "asset-b", "asset-a"]);

    rerender(<MediaCollectionNode node={collectionNode("image", changes.at(-1))} onItemsChange={(next) => changes.push(next)} />);
    fireEvent.click(screen.getByRole("button", { name: "移除 图片2" }));
    expect(changes.at(-1)?.map((item) => item.assetId)).toEqual(["asset-c", "asset-a"]);
});

it("uploads multiple files with progress, persists only active safe assets in selection order, and revokes previews", async () => {
    let finishFirst: (() => void) | undefined;
    let finishSecond: (() => void) | undefined;
    const upload = vi.fn((file: File, mediaType: GraphMediaType, onProgress: (percent: number) => void) => {
        onProgress(file.name === "first.png" ? 25 : 60);
        return new Promise<OwnedMediaAsset>((resolve) => {
            const finish = () => resolve({
                id: file.name === "first.png" ? "asset-first" : "asset-second",
                kind: "reference" as const,
                status: "active" as const,
                media_type: mediaType,
                mime_type: file.type,
                size_bytes: file.size,
                content_url: `/api/v1/assets/${file.name === "first.png" ? "asset-first" : "asset-second"}/content`,
            });
            if (file.name === "first.png") finishFirst = finish;
            else finishSecond = finish;
        });
    });
    const createObjectURL = vi.fn((file: File) => `blob:${file.name}`);
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const changes: GraphMediaItem[][] = [];
    render(<MediaCollectionNode node={collectionNode("image", [])} upload={upload} onItemsChange={(next) => changes.push(next)} />);

    fireEvent.change(screen.getByLabelText("添加图片"), { target: { files: [
        new File(["a"], "first.png", { type: "image/png" }),
        new File(["bb"], "second.png", { type: "image/png" }),
    ] } });

    expect(await screen.findByText("first.png · 25%")).toBeVisible();
    expect(screen.getByText("second.png · 60%")).toBeVisible();
    finishSecond?.();
    finishFirst?.();
    await waitFor(() => expect(changes.at(-1)?.map((item) => item.assetId)).toEqual(["asset-first", "asset-second"]));
    expect(changes.at(-1)?.map((item) => item.displayName)).toEqual(["first.png", "second.png"]);
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledTimes(2));
});

it("keeps successful uploads, shows a safe error for failed files, and does not persist failures", async () => {
    const upload = vi.fn(async (file: File, mediaType: GraphMediaType) => {
        if (file.name === "bad.mp4") throw new Error("secret upstream stack");
        return { id: "asset-good", kind: "reference" as const, status: "active" as const, media_type: mediaType, mime_type: file.type, size_bytes: file.size, content_url: "/api/v1/assets/asset-good/content" };
    });
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:preview"), revokeObjectURL: vi.fn() });
    const changes: GraphMediaItem[][] = [];
    render(<MediaCollectionNode node={collectionNode("video", [])} upload={upload} onItemsChange={(next) => changes.push(next)} />);

    fireEvent.change(screen.getByLabelText("添加视频"), { target: { files: [
        new File(["good"], "good.mp4", { type: "video/mp4" }),
        new File(["bad"], "bad.mp4", { type: "video/mp4" }),
    ] } });

    expect(await screen.findByText("bad.mp4 上传失败，请重试。")).toBeVisible();
    expect(screen.queryByText(/secret upstream stack/)).not.toBeInTheDocument();
    expect(changes.at(-1)?.map((item) => item.assetId)).toEqual(["asset-good"]);
});

it("keeps previews available but removes all mutation controls in read-only mode", () => {
    render(<MediaCollectionNode node={collectionNode("audio", [{ ...items[0], mimeType: "audio/mpeg", displayName: "voice.mp3" }])} readOnly onItemsChange={() => { throw new Error("must not mutate"); }} />);

    expect(screen.getByLabelText("音频1 voice.mp3")).toHaveAttribute("src", "/api/v1/assets/asset-a/content");
    expect(screen.queryByLabelText("添加音频")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /移除|上移|下移/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("media-item-item-a")).not.toHaveAttribute("draggable", "true");
});

it("uploads through same-origin XHR with CSRF and reports bounded progress", async () => {
    class FakeXhr {
        static instance: FakeXhr;
        uploadListeners: Record<string, (event: ProgressEvent) => void> = {};
        listeners: Record<string, () => void> = {};
        headers: Record<string, string> = {};
        status = 201;
        responseText = "";
        withCredentials = false;
        method = "";
        url = "";
        upload = { addEventListener: (name: string, callback: (event: ProgressEvent) => void) => { this.uploadListeners[name] = callback; } };
        constructor() { FakeXhr.instance = this; }
        open(method: string, url: string) { this.method = method; this.url = url; }
        setRequestHeader(name: string, value: string) { this.headers[name] = value; }
        addEventListener(name: string, callback: () => void) { this.listeners[name] = callback; }
        send(body: FormData) {
            expect(body.get("media_type")).toBe("image");
            this.uploadListeners.progress(new ProgressEvent("progress", { lengthComputable: true, loaded: 1, total: 4 }));
            this.responseText = JSON.stringify({ asset_id: "asset-x", kind: "reference", status: "active", media_type: "image", mime_type: "image/png", size_bytes: 4, created_at: "2026-08-11T00:00:00Z", content_url: "/api/v1/assets/asset-x/content" });
            this.listeners.load();
        }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    setCsrfToken("csrf-123");
    const progress: number[] = [];

    const result = await uploadMediaAsset(new File(["data"], "frame.png", { type: "image/png" }), "image", (percent) => progress.push(percent));

    expect(FakeXhr.instance.method).toBe("POST");
    expect(FakeXhr.instance.url).toBe("/api/v1/assets");
    expect(FakeXhr.instance.withCredentials).toBe(true);
    expect(FakeXhr.instance.headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(progress).toEqual([25, 100]);
    expect(result).toMatchObject({ id: "asset-x", media_type: "image", content_url: "/api/v1/assets/asset-x/content" });
});

it("normalizes safe server asset metadata for portrait polling without exposing storage fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
        asset_id: "portrait-x", kind: "portrait", status: "processing", media_type: "image", mime_type: "image/png",
        size_bytes: 42, created_at: "2026-08-11T00:00:00Z", content_url: "/api/v1/assets/portrait-x/content", relative_path: "must-not-pass",
    }), { headers: { "content-type": "application/json" } })));

    const result = await fetchAsset("portrait-x");

    expect(result).toEqual({ id: "portrait-x", kind: "portrait", status: "processing", media_type: "image", mime_type: "image/png", size_bytes: 42, content_url: "/api/v1/assets/portrait-x/content" });
    expect(result).not.toHaveProperty("relative_path");
});

it("creates all three collection nodes and persists uploaded asset order in the canvas project", async () => {
    let uploadNumber = 0;
    class FakeXhr {
        status = 201;
        responseText = "";
        withCredentials = false;
        upload = { addEventListener: () => undefined };
        listeners: Record<string, () => void> = {};
        open() { return undefined; }
        setRequestHeader() { return undefined; }
        addEventListener(name: string, callback: () => void) { this.listeners[name] = callback; }
        send(body: FormData) {
            uploadNumber += 1;
            const file = body.get("file") as File;
            const assetId = `asset-${uploadNumber}`;
            this.responseText = JSON.stringify({ asset_id: assetId, kind: "reference", status: "active", media_type: "image", mime_type: file.type, size_bytes: file.size, content_url: `/api/v1/assets/${assetId}/content` });
            this.listeners.load();
        }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn((file: File) => `blob:${file.name}`), revokeObjectURL: vi.fn() });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ models: [] }), { headers: { "content-type": "application/json" } })));
    const projectId = useCanvasStore.getState().createProject("Media Canvas");
    render(<MemoryRouter initialEntries={[`/canvas/${projectId}`]}><Routes><Route path="/canvas/:id" element={<CanvasProjectPage />} /></Routes></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "参考图节点" }));
    fireEvent.change(screen.getByLabelText("添加图片"), { target: { files: [
        new File(["a"], "first.png", { type: "image/png" }),
        new File(["bb"], "second.png", { type: "image/png" }),
    ] } });
    await waitFor(() => {
        const imageCollection = useCanvasStore.getState().openProject(projectId)?.nodes.find((node) => node.metadata?.graph?.role === "media-collection" && node.metadata.graph.mediaType === "image");
        expect(imageCollection?.metadata?.graph?.role === "media-collection" ? imageCollection.metadata.graph.items.map((item) => item.assetId) : []).toEqual(["asset-1", "asset-2"]);
    });
    fireEvent.click(screen.getByRole("button", { name: "上移 图片2" }));
    await waitFor(() => {
        const imageCollection = useCanvasStore.getState().openProject(projectId)?.nodes.find((node) => node.metadata?.graph?.role === "media-collection" && node.metadata.graph.mediaType === "image");
        expect(imageCollection?.metadata?.graph?.role === "media-collection" ? imageCollection.metadata.graph.items.map((item) => item.assetId) : []).toEqual(["asset-2", "asset-1"]);
    });

    fireEvent.click(screen.getByRole("button", { name: "参考视频节点" }));
    fireEvent.click(screen.getByRole("button", { name: "参考音频节点" }));
    const mediaTypes = useCanvasStore.getState().openProject(projectId)?.nodes.flatMap((node) => node.metadata?.graph?.role === "media-collection" ? [node.metadata.graph.mediaType] : []);
    expect(mediaTypes).toEqual(["image", "video", "audio"]);
});
