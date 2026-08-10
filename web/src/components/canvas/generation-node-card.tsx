import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";

import type { CanvasNodeData } from "@/types/canvas";


export function GenerationNodeCard({ node, onRetry }: { node: CanvasNodeData; onRetry?: (token: string) => void }) {
    const status = node.metadata?.status || "idle";
    const result = node.metadata?.sourceJobId;
    return <article data-node-id={node.id} data-testid={result ? `result-node-${result}` : `generation-node-${node.id}`} className="absolute overflow-hidden rounded-xl border border-[#285038] bg-[#0a140e] text-xs text-[#dceee1] shadow-xl" style={{ left: node.position.x, top: node.position.y, width: node.width, minHeight: node.height }}>
        <header className="flex items-center gap-2 border-b border-[#1c3826] px-3 py-2"><span className="text-[#58ed87]">{status === "success" ? <CircleCheck className="size-4" /> : status === "error" ? <CircleX className="size-4 text-[#ff8c82]" /> : <LoaderCircle className="size-4 animate-spin" />}</span><strong>{node.title}</strong></header>
        {status === "success" && node.type === "image" && node.metadata?.content ? <div className="media-surface m-3 overflow-hidden rounded-lg"><img src={node.metadata.content} alt="生成结果" className="block h-auto w-full" /></div> : <div className="space-y-2 p-3"><p className="whitespace-pre-wrap text-[#b8cdbd]">{status === "error" ? node.metadata?.errorDetails : node.metadata?.prompt || node.metadata?.content}</p>{node.metadata?.requestId ? <p className="text-[#688371]">请求编号：{node.metadata.requestId}</p> : null}{status === "error" && node.metadata?.idempotencyKey && onRetry ? <button className="rounded border border-[#356b48] px-2 py-1 text-[#65e98d]" onClick={() => onRetry(node.metadata!.idempotencyKey!)}>重试</button> : null}</div>}
    </article>;
}
