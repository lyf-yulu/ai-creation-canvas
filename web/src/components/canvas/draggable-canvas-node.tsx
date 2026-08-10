import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import type { CanvasNodeData, Position } from "@/types/canvas";

type DraggableCanvasNodeProps = {
    node: CanvasNodeData;
    scale: number;
    onPositionChange: (nodeId: string, position: Position) => void;
    children: ReactNode;
};

type DragState = {
    active: boolean;
    pointerId: number | null;
    startX: number;
    startY: number;
    initial: Position;
    scale: number;
    previousCursor: string;
};

const interactiveSelector = "button,input,textarea,select,a,video,audio";

function normalizedScale(scale: number) {
    return Number.isFinite(scale) && scale > 0 ? scale : 1;
}

export function DraggableCanvasNode({ node, scale, onPositionChange, children }: DraggableCanvasNodeProps) {
    const dragRef = useRef<DragState>({
        active: false,
        pointerId: null,
        startX: 0,
        startY: 0,
        initial: node.position,
        scale: 1,
        previousCursor: "",
    });
    const frameRef = useRef<number | null>(null);
    const nextPositionRef = useRef<Position | null>(null);
    const onPositionChangeRef = useRef(onPositionChange);
    const nodeIdRef = useRef(node.id);
    const finishDragRef = useRef<((pointerId?: number, flush?: boolean) => void) | null>(null);
    onPositionChangeRef.current = onPositionChange;
    nodeIdRef.current = node.id;

    useEffect(() => {
        return () => finishDragRef.current?.(undefined, false);
    }, []);

    const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || dragRef.current.active) return;
        const target = event.target instanceof Element ? event.target : null;
        if (target?.closest(interactiveSelector)) return;

        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        dragRef.current = {
            active: true,
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            initial: node.position,
            scale: normalizedScale(scale),
            previousCursor: document.body.style.cursor,
        };
        document.body.style.cursor = "grabbing";

        const emitPendingPosition = () => {
            const nextPosition = nextPositionRef.current;
            nextPositionRef.current = null;
            if (nextPosition) onPositionChangeRef.current(nodeIdRef.current, nextPosition);
        };

        let listening = true;
        const detachListeners = () => {
            if (!listening) return;
            listening = false;
            window.removeEventListener("pointermove", handlePointerMove);
            window.removeEventListener("pointerup", handlePointerUp);
            window.removeEventListener("pointercancel", handlePointerCancel);
            window.removeEventListener("blur", handleWindowBlur);
        };
        const finishDrag = (pointerId?: number, flush = true) => {
            const drag = dragRef.current;
            if (!drag.active || (pointerId !== undefined && pointerId !== drag.pointerId)) return;

            if (frameRef.current !== null) {
                cancelAnimationFrame(frameRef.current);
                frameRef.current = null;
            }
            drag.active = false;
            drag.pointerId = null;
            document.body.style.cursor = drag.previousCursor;
            detachListeners();
            finishDragRef.current = null;
            if (flush) emitPendingPosition();
            else nextPositionRef.current = null;
        };

        const handlePointerMove = (event: PointerEvent) => {
            const drag = dragRef.current;
            if (!drag.active || event.pointerId !== drag.pointerId) return;

            const position = {
                x: drag.initial.x + (event.clientX - drag.startX) / drag.scale,
                y: drag.initial.y + (event.clientY - drag.startY) / drag.scale,
            };
            if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) return;

            nextPositionRef.current = position;
            if (frameRef.current !== null) return;
            frameRef.current = requestAnimationFrame(() => {
                frameRef.current = null;
                emitPendingPosition();
            });
        };

        const handlePointerUp = (event: PointerEvent) => finishDrag(event.pointerId);
        const handlePointerCancel = (event: PointerEvent) => finishDrag(event.pointerId);
        const handleWindowBlur = () => finishDrag();

        window.addEventListener("pointermove", handlePointerMove);
        window.addEventListener("pointerup", handlePointerUp);
        window.addEventListener("pointercancel", handlePointerCancel);
        window.addEventListener("blur", handleWindowBlur);
        finishDragRef.current = finishDrag;
    };

    return (
        <div
            data-node-id={node.id}
            data-testid={`draggable-node-${node.id}`}
            className="absolute"
            style={{ left: node.position.x, top: node.position.y, width: node.width, minHeight: node.height }}
            onPointerDown={handlePointerDown}
        >
            {children}
        </div>
    );
}
