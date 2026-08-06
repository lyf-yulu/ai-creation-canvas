import { useParams } from "react-router-dom";
import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { useRef, useState } from "react";
import type { ViewportTransform } from "@/types/canvas";

/** A local canvas shell. Generation is submitted only through the same-origin task client. */
export default function CanvasProjectPage() {
    const { id } = useParams();
    const containerRef = useRef<HTMLDivElement>(null);
    const [viewport, setViewport] = useState<ViewportTransform>({ x: 0, y: 0, k: 1 });
    return <main className="h-full"><InfiniteCanvas containerRef={containerRef} viewport={viewport} onViewportChange={setViewport}><section className="m-8 rounded-xl border bg-background p-5 shadow-sm"><h1 className="text-lg font-medium">画布 {id}</h1><p className="mt-2 text-sm text-stone-500">生成操作仅会通过受控的同源任务 API 提交。</p></section></InfiniteCanvas></main>;
}
