import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, GripVertical, Plus, Trash2 } from "lucide-react";
import { nanoid } from "nanoid";

import { uploadMediaAsset } from "@/api/assets";
import type { OwnedMediaAsset } from "@/api/contracts";
import type { GraphMediaItem, GraphMediaType } from "@/features/graph/contracts";
import { mediaItemLabel, moveMediaItem, moveMediaItemTo, safeMediaDisplayName } from "@/features/graph/media-collection";
import type { CanvasNodeData } from "@/types/canvas";

type UploadFunction = (file: File, mediaType: GraphMediaType, onProgress: (percent: number) => void) => Promise<OwnedMediaAsset>;

type MediaCollectionNodeProps = {
    node: CanvasNodeData;
    readOnly?: boolean;
    onItemsChange: (items: GraphMediaItem[]) => void;
    upload?: UploadFunction;
};

type PendingUpload = {
    id: string;
    name: string;
    progress: number;
    previewUrl: string | null;
    failed: boolean;
};

const copyByType: Readonly<Record<GraphMediaType, { noun: string; accept: string }>> = {
    image: { noun: "图片", accept: "image/png,image/jpeg,image/webp" },
    video: { noun: "视频", accept: "video/mp4,video/webm" },
    audio: { noun: "音频", accept: "audio/mpeg,audio/wav" },
};

function MediaPreview({ mediaType, item, label }: { mediaType: GraphMediaType; item: GraphMediaItem; label: string }) {
    const source = `/api/v1/assets/${encodeURIComponent(item.assetId)}/content`;
    const accessibleName = `${label} ${item.displayName}`;
    if (mediaType === "image") return <img src={source} alt={accessibleName} className="h-16 w-20 rounded-md border border-[#294936] object-cover" />;
    if (mediaType === "video") return <video src={source} aria-label={accessibleName} controls preload="metadata" className="h-16 w-24 rounded-md border border-[#294936] bg-black object-cover" />;
    return <audio src={source} aria-label={accessibleName} controls preload="metadata" className="h-9 w-40 max-w-full" />;
}

export function MediaCollectionNode({ node, readOnly = false, onItemsChange, upload = uploadMediaAsset }: MediaCollectionNodeProps) {
    const graph = node.metadata?.graph;
    if (graph?.role !== "media-collection") return null;
    const { mediaType, items } = graph;
    const details = copyByType[mediaType];
    const [pending, setPending] = useState<PendingUpload[]>([]);
    const itemsRef = useRef(items);
    const mountedRef = useRef(true);
    const objectUrlsRef = useRef(new Set<string>());
    const draggedItemRef = useRef<string | null>(null);
    itemsRef.current = items;

    useEffect(() => () => {
        mountedRef.current = false;
        for (const url of objectUrlsRef.current) URL.revokeObjectURL(url);
        objectUrlsRef.current.clear();
    }, []);

    const handleFiles = async (files: File[]) => {
        if (readOnly || files.length === 0) return;
        const batch = files.map((file) => {
            const previewUrl = typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : null;
            if (previewUrl) objectUrlsRef.current.add(previewUrl);
            return { id: nanoid(), file, previewUrl };
        });
        setPending((current) => [...current, ...batch.map(({ id, file, previewUrl }) => ({ id, name: safeMediaDisplayName(file.name, mediaType), progress: 0, previewUrl, failed: false }))]);
        const results = await Promise.all(batch.map(async ({ id, file }) => {
            try {
                const asset = await upload(file, mediaType, (progress) => {
                    if (mountedRef.current) setPending((current) => current.map((entry) => entry.id === id ? { ...entry, progress } : entry));
                });
                if (asset.status !== "active" || asset.media_type !== mediaType) throw new Error("inactive asset");
                return { id, file, asset } as const;
            } catch {
                return { id, file, asset: null } as const;
            }
        }));
        for (const { previewUrl } of batch) {
            if (previewUrl) {
                URL.revokeObjectURL(previewUrl);
                objectUrlsRef.current.delete(previewUrl);
            }
        }
        if (!mountedRef.current) return;
        const accepted = results.flatMap(({ file, asset }) => asset ? [{
            id: nanoid(),
            assetId: asset.id,
            displayName: safeMediaDisplayName(file.name, mediaType),
            mimeType: asset.mime_type,
            bytes: asset.size_bytes,
        }] : []);
        if (accepted.length) onItemsChange([...itemsRef.current, ...accepted]);
        const failures = new Set(results.filter((result) => !result.asset).map((result) => result.id));
        setPending((current) => current.flatMap((entry) => failures.has(entry.id) ? [{ ...entry, failed: true, previewUrl: null }] : []));
    };

    return <article className="overflow-hidden rounded-xl border border-[#285039] bg-[#09140d] text-[#dceee1] shadow-[0_12px_36px_rgba(0,0,0,0.36)]">
        <header className="flex items-center justify-between border-b border-[#203e2c] px-3 py-2">
            <div><p className="text-[10px] tracking-[0.16em] text-[#58ed87]">MEDIA INPUT</p><h2 className="text-sm font-semibold">{node.title}</h2></div>
            {!readOnly ? <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-[#356b48] bg-[#102319] px-2 py-1 text-xs text-[#bcebc9] hover:border-[#58ed87]">
                <Plus className="size-3.5" />添加
                <input
                    className="sr-only"
                    type="file"
                    multiple
                    accept={details.accept}
                    aria-label={`添加${details.noun}`}
                    onChange={(event) => {
                        const selected = Array.from(event.currentTarget.files ?? []);
                        event.currentTarget.value = "";
                        void handleFiles(selected);
                    }}
                />
            </label> : null}
        </header>
        <ol className="max-h-80 space-y-2 overflow-y-auto p-2">
            {items.map((item, index) => {
                const label = mediaItemLabel(mediaType, index);
                return <li
                    key={item.id}
                    data-testid={`media-item-${item.id}`}
                    draggable={!readOnly || undefined}
                    onDragStart={() => { if (!readOnly) draggedItemRef.current = item.id; }}
                    onDragOver={(event) => { if (!readOnly) event.preventDefault(); }}
                    onDrop={(event) => {
                        if (readOnly) return;
                        event.preventDefault();
                        const dragged = draggedItemRef.current;
                        draggedItemRef.current = null;
                        if (dragged) onItemsChange([...moveMediaItemTo(items, dragged, item.id)]);
                    }}
                    className="flex items-center gap-2 rounded-lg border border-[#1e3a29] bg-[#0d1b12] p-2"
                >
                    {!readOnly ? <GripVertical className="size-4 shrink-0 text-[#647b6a]" aria-hidden="true" /> : null}
                    <MediaPreview mediaType={mediaType} item={item} label={label} />
                    <div className="min-w-0 flex-1"><p className="text-xs font-medium text-[#bcebc9]">{label}</p><p className="truncate text-[11px] text-[#829889]">{item.displayName}</p></div>
                    {!readOnly ? <div className="flex shrink-0 items-center gap-1">
                        <button type="button" aria-label={`上移 ${label}`} disabled={index === 0} onClick={() => onItemsChange([...moveMediaItem(items, item.id, -1)])} className="rounded p-1 text-[#9db4a3] hover:bg-[#183322] disabled:opacity-30"><ChevronUp className="size-3.5" /></button>
                        <button type="button" aria-label={`下移 ${label}`} disabled={index === items.length - 1} onClick={() => onItemsChange([...moveMediaItem(items, item.id, 1)])} className="rounded p-1 text-[#9db4a3] hover:bg-[#183322] disabled:opacity-30"><ChevronDown className="size-3.5" /></button>
                        <button type="button" aria-label={`移除 ${label}`} onClick={() => onItemsChange(items.filter((candidate) => candidate.id !== item.id))} className="rounded p-1 text-[#e7a69a] hover:bg-[#3a1e1b]"><Trash2 className="size-3.5" /></button>
                    </div> : null}
                </li>;
            })}
            {pending.map((entry) => <li key={entry.id} role={entry.failed ? "alert" : "status"} className={`rounded-lg border px-3 py-2 text-xs ${entry.failed ? "border-[#744038] bg-[#281411] text-[#ffc0b5]" : "border-[#355f43] bg-[#102319] text-[#bcebc9]"}`}>
                {entry.failed ? `${entry.name} 上传失败，请重试。` : `${entry.name} · ${entry.progress}%`}
            </li>)}
            {items.length === 0 && pending.length === 0 ? <li className="rounded-lg border border-dashed border-[#31523c] px-4 py-7 text-center text-xs text-[#829889]">添加一个或多个{details.noun}，顺序会决定 @引用编号。</li> : null}
        </ol>
    </article>;
}
