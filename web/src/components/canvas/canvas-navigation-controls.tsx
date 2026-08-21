import { MAX_CANVAS_SCALE, MIN_CANVAS_SCALE, RESET_VIEWPORT } from "@/features/canvas/viewport";
import type { ViewportTransform } from "@/types/canvas";

type Props = {
    viewport: ViewportTransform;
    onViewportChange: (viewport: ViewportTransform) => void;
};

export function CanvasNavigationControls({ viewport, onViewportChange }: Props) {
    const scalePercent = Math.round(viewport.k * 100);

    return (
        <div data-canvas-no-zoom className="absolute bottom-4 left-4 z-20 flex max-w-[calc(100%-2rem)] items-center gap-2 rounded-xl border border-[var(--c-border)] bg-[var(--c-panel)e8] px-3 py-2 shadow-xl">
            <button type="button" aria-label="复位画布" className="rounded-md px-2 py-1 text-xs text-[var(--c-text)] hover:bg-[var(--c-border)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--c-accent)]" onClick={() => onViewportChange({ ...RESET_VIEWPORT })}>复位</button>
            <input aria-label="画布缩放" type="range" min={MIN_CANVAS_SCALE * 100} max={MAX_CANVAS_SCALE * 100} value={scalePercent} className="w-20 accent-[var(--c-accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--c-accent)]" onChange={(event) => onViewportChange({ ...viewport, k: Number(event.target.value) / 100 })} />
            <span aria-live="polite" className="w-10 text-right text-xs tabular-nums text-[var(--c-text-3)]">{scalePercent}%</span>
        </div>
    );
}
