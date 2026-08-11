import { useEffect, useLayoutEffect, useRef, useState, type ChangeEvent, type PointerEvent } from "react";
import { FileText } from "lucide-react";

import type { CanvasNodeData } from "@/types/canvas";

const MAX_PROMPT_FILE_BYTES = 1024 * 1024;

type PromptNodeCardProps = {
    node: CanvasNodeData;
    disabled?: boolean;
    onTextChange: (text: string) => void;
};

export function PromptNodeCard({ node, disabled = false, onTextChange }: PromptNodeCardProps) {
    const graph = node.metadata?.graph;
    const text = graph?.role === "prompt" ? graph.text : node.metadata?.content ?? "";
    const [error, setError] = useState<string | null>(null);
    const mountedRef = useRef(true);
    const importSequenceRef = useRef(0);
    const nodeIdRef = useRef(node.id);
    const disabledRef = useRef(disabled);

    useLayoutEffect(() => {
        if (nodeIdRef.current === node.id) return;
        nodeIdRef.current = node.id;
        importSequenceRef.current += 1;
    }, [node.id]);

    useLayoutEffect(() => {
        if (disabled && !disabledRef.current) importSequenceRef.current += 1;
        disabledRef.current = disabled;
    }, [disabled]);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            importSequenceRef.current += 1;
        };
    }, []);

    const importTxt = async (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file || disabled) return;
        const sequence = importSequenceRef.current + 1;
        importSequenceRef.current = sequence;
        const sourceNodeId = node.id;
        const isLatest = () => mountedRef.current && !disabledRef.current && importSequenceRef.current === sequence && nodeIdRef.current === sourceNodeId;
        setError(null);
        if (!file.name.toLocaleLowerCase().endsWith(".txt") || (file.type && file.type !== "text/plain")) {
            if (isLatest()) setError("请选择纯文本 TXT 文件。");
            return;
        }
        if (file.size > MAX_PROMPT_FILE_BYTES) {
            if (isLatest()) setError("TXT 文件不能超过 1 MB。");
            return;
        }
        let bytes: ArrayBuffer;
        try {
            bytes = await file.arrayBuffer();
        } catch {
            if (isLatest()) setError("无法读取这个 TXT 文件，请重新选择。");
            return;
        }
        if (!isLatest()) return;
        try {
            const imported = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
            if (isLatest()) onTextChange(imported.replace(/^\uFEFF/, ""));
        } catch {
            if (isLatest()) setError("TXT 文件必须使用 UTF-8 编码。");
        }
    };

    const stopEditingGesture = (event: PointerEvent<HTMLElement>) => event.stopPropagation();

    return (
        <article className="max-w-full overflow-hidden rounded-xl border border-[#285038] bg-[#0a140e] text-xs text-[#dceee1] shadow-xl">
            <header className="flex items-center gap-2 border-b border-[#1c3826] px-3 py-2">
                <FileText className="size-4 text-[#58ed87]" />
                <strong>{node.title}</strong>
            </header>
            <div className="space-y-3 p-3">
                <label className="block text-[11px] text-[#8fa596]" htmlFor={`prompt-node-${node.id}`}>提示词内容</label>
                <textarea
                    id={`prompt-node-${node.id}`}
                    aria-label="提示词内容"
                    disabled={disabled}
                    value={text}
                    placeholder="在这里输入提示词，也可以导入本地 TXT 文件"
                    className="min-h-24 w-full resize-y rounded-lg border border-[#285038] bg-[#050806] p-2.5 text-sm leading-6 text-[#dceee1] placeholder:text-[#58705f] disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#58ed87]"
                    onPointerDown={stopEditingGesture}
                    onChange={(event) => onTextChange(event.target.value)}
                />
                <label className="block text-[11px] text-[#8fa596]">
                    导入 TXT
                    <input
                        aria-label="导入 TXT"
                        disabled={disabled}
                        type="file"
                        accept="text/plain,.txt"
                        className="mt-1 block w-full max-w-full text-[11px] text-[#9fb5a5] file:mr-2 file:rounded-md file:border file:border-[#356b48] file:bg-[#102319] file:px-2 file:py-1 file:text-[#65e98d] disabled:opacity-60"
                        onPointerDown={stopEditingGesture}
                        onChange={(event) => void importTxt(event)}
                    />
                </label>
                {error ? <p role="alert" className="rounded-md border border-[#743c36] bg-[#2a110f] px-2 py-1.5 text-[#ff9b91]">{error}</p> : null}
            </div>
        </article>
    );
}
