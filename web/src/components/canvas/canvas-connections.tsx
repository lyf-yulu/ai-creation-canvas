import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { connectionPathData, getNodePortAnchor } from "@/lib/canvas/canvas-node-geometry";
import { canvasThemes } from "@/lib/canvas-theme";
import { useThemeStore } from "@/stores/use-theme-store";
import type { CanvasConnection, CanvasNodeData, ConnectionHandle, Position } from "@/types/canvas";

export function ConnectionPath({
    connection,
    from,
    to,
    active,
    interactive = true,
    onSelect,
    onOpenContextMenu,
}: {
    connection: CanvasConnection;
    from: CanvasNodeData;
    to: CanvasNodeData;
    active: boolean;
    interactive?: boolean;
    onSelect: () => void;
    onOpenContextMenu?: (position: { x: number; y: number }, trigger: SVGPathElement) => void;
}) {
    const theme = canvasThemes[useThemeStore((state) => state.theme)];
    const pathD = connectionPathData(from, connection.fromPortId, to, connection.toPortId);
    const selectWithKeyboard = (event: ReactKeyboardEvent<SVGPathElement>) => {
        if ((event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) && onOpenContextMenu) {
            event.preventDefault();
            event.stopPropagation();
            const rect = event.currentTarget.getBoundingClientRect();
            onOpenContextMenu({ x: rect.left + Math.min(rect.width, 24), y: rect.top + Math.min(rect.height, 24) }, event.currentTarget);
            return;
        }
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        event.stopPropagation();
        onSelect();
    };

    return (
        <g>
            {interactive ? <path
                data-connection-id={connection.id}
                role="button"
                aria-label={`连接：${from.title} 到 ${to.title}`}
                aria-selected={active}
                tabIndex={0}
                d={pathD}
                stroke="transparent"
                strokeWidth="16"
                fill="none"
                style={{ cursor: "pointer", pointerEvents: "stroke" }}
                onClick={(event) => {
                    event.stopPropagation();
                    onSelect();
                }}
                onKeyDown={selectWithKeyboard}
                onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onOpenContextMenu?.({ x: event.clientX, y: event.clientY }, event.currentTarget);
                }}
            /> : null}
            <path
                data-connection-id={interactive ? undefined : connection.id}
                aria-hidden="true"
                d={pathD}
                stroke={active ? theme.node.activeStroke : theme.node.muted}
                strokeWidth={active ? 3 : 2}
                strokeOpacity={active ? 1 : 0.82}
                fill="none"
                style={{ filter: active ? `drop-shadow(0 0 8px ${theme.node.activeStroke}66)` : undefined, pointerEvents: "none" }}
            />
        </g>
    );
}

export function ActiveConnectionPath({ node, handle, mouseWorld, target }: { node?: CanvasNodeData; handle: ConnectionHandle; mouseWorld: Position; target?: CanvasNodeData }) {
    const theme = canvasThemes[useThemeStore((state) => state.theme)];
    if (!node) return null;

    const namedAnchor = handle.portId ? getNodePortAnchor(node, handle.portId, handle.handleType) : null;
    const startX = handle.handleType === "source" ? namedAnchor?.x ?? node.position.x + node.width : mouseWorld.x;
    const startY = handle.handleType === "source" ? namedAnchor?.y ?? node.position.y + node.height / 2 : mouseWorld.y;
    const endX = handle.handleType === "source" ? mouseWorld.x : node.position.x;
    const endY = handle.handleType === "source" ? mouseWorld.y : node.position.y + node.height / 2;
    const snappedStartX = handle.handleType === "target" && target ? target.position.x + target.width : startX;
    const snappedStartY = handle.handleType === "target" && target ? target.position.y + target.height / 2 : startY;
    const snappedEndX = handle.handleType === "source" && target ? target.position.x : endX;
    const snappedEndY = handle.handleType === "source" && target ? target.position.y + target.height / 2 : endY;
    const distance = Math.abs(snappedEndX - snappedStartX);
    const pathD = `M ${snappedStartX} ${snappedStartY} C ${snappedStartX + distance * 0.5} ${snappedStartY}, ${snappedEndX - distance * 0.5} ${snappedEndY}, ${snappedEndX} ${snappedEndY}`;

    return <path d={pathD} stroke={theme.node.activeStroke} strokeWidth="2" fill="none" strokeDasharray="5,5" />;
}
